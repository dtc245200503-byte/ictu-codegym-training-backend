from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.dependencies import require_roles
from app.core.security import DEFAULT_ACCOUNT_PASSWORD, hash_password
from app.db.session import get_db
from app.models.lead import Lead, LeadActivity
from app.models.notification import Notification
from app.models.role import Role
from app.models.student_profile import StudentProfile
from app.models.user import User
from app.schemas.leads import LeadActivityCreate, LeadCreate, LeadUpdate

router = APIRouter(prefix="/leads", tags=["Admissions leads"])


def _utc_datetime_payload(value: datetime | None) -> str | None:
    return f"{value.isoformat()}Z" if value is not None else None


def _lead_payload(lead: Lead) -> dict:
    return {
        "id": lead.id,
        "full_name": lead.full_name,
        "email": lead.email,
        "phone": lead.phone,
        "interested_program": lead.interested_program,
        "message": lead.message,
        "status": lead.status,
        "notes": lead.notes,
        "rejection_reason": lead.rejection_reason,
        "assigned_to_user_id": lead.assigned_to_user_id,
        "converted_user_id": lead.converted_user_id,
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        "updated_at": lead.updated_at.isoformat() if lead.updated_at else None,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_lead(request: LeadCreate, db: Session = Depends(get_db)) -> dict:
    lead = Lead(
        full_name=request.full_name,
        email=str(request.email),
        phone=request.phone,
        interested_program=request.interested_program,
        message=request.message,
    )
    db.add(lead)
    db.flush()
    db.add(
        LeadActivity(
            lead_id=lead.id,
            message="Tiếp nhận đăng ký tư vấn trực tuyến.",
        )
    )
    db.commit()
    db.refresh(lead)
    return _lead_payload(lead)


@router.get("")
def list_leads(
    q: str = Query(default="", max_length=150),
    lead_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(new|contacted|consulting|trial_scheduled|closed|rejected|enrolled|qualified)$",
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    _admissions: User = Depends(require_roles("admin", "admissions")),
    db: Session = Depends(get_db),
) -> dict:
    query = select(Lead)
    if lead_status:
        query = query.where(Lead.status == lead_status)
    if q.strip():
        term = f"%{q.strip()}%"
        query = query.where(
            or_(
                Lead.full_name.ilike(term),
                Lead.email.ilike(term),
                Lead.phone.ilike(term),
                Lead.interested_program.ilike(term),
            )
        )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    leads = db.scalars(
        query.order_by(Lead.created_at.desc(), Lead.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [_lead_payload(lead) for lead in leads],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.patch("/{lead_id}")
def update_lead(
    lead_id: int,
    request: LeadUpdate,
    admissions_user: User = Depends(require_roles("admin", "admissions")),
    db: Session = Depends(get_db),
) -> dict:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ tư vấn.")
    if lead.status == "enrolled":
        raise HTTPException(
            status_code=409, detail="Lead đã nhập học và không thể chỉnh sửa."
        )
    updates = request.model_dump(exclude_unset=True)
    previous_notes = lead.notes
    previous_status = lead.status
    previous_assignee = lead.assigned_to_user_id
    if request.assigned_to_user_id is not None:
        assignee = db.scalar(
            select(User)
            .options(selectinload(User.roles))
            .where(
                User.id == request.assigned_to_user_id,
                User.is_active.is_(True),
                User.is_locked.is_(False),
            )
        )
        if assignee is None or not any(
            role.code == "admissions" for role in assignee.roles
        ):
            raise HTTPException(
                status_code=422,
                detail="Người được giao phải là tài khoản Admissions đang hoạt động.",
            )
    for field, value in updates.items():
        setattr(lead, field, value)
    lead.updated_by_id = admissions_user.id
    if (
        "assigned_to_user_id" in updates
        and lead.assigned_to_user_id != previous_assignee
        and lead.assigned_to_user_id is not None
    ):
        db.add(
            Notification(
                user_id=lead.assigned_to_user_id,
                lead_id=lead.id,
                title="Lead được giao cho bạn",
                message=f"Bạn được giao tư vấn hồ sơ {lead.full_name}.",
            )
        )
    if request.status == "rejected" and previous_status != "rejected":
        lead.rejection_reason = request.rejection_reason
    if request.status is not None and request.status != previous_status:
        db.add(
            LeadActivity(
                lead_id=lead.id,
                actor_user_id=admissions_user.id,
                message=f"Cập nhật trạng thái: {previous_status} → {request.status}.",
                activity_type="other",
            )
        )
    if "rejection_reason" in updates and request.rejection_reason:
        lead.rejection_reason = request.rejection_reason
    if "notes" in updates and request.notes and request.notes != previous_notes:
        db.add(
            LeadActivity(
                lead_id=lead.id,
                actor_user_id=admissions_user.id,
                message=f"Ghi chú chăm sóc: {request.notes}",
            )
        )
    db.commit()
    db.refresh(lead)
    return _lead_payload(lead)


@router.get("/{lead_id}/activities")
def list_lead_activities(
    lead_id: int,
    _admissions: User = Depends(require_roles("admin", "admissions")),
    db: Session = Depends(get_db),
) -> list[dict]:
    if db.get(Lead, lead_id) is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ tư vấn.")
    return [
        {
            "id": activity.id,
            "message": activity.message,
            "activity_type": activity.activity_type,
            "actor_user_id": activity.actor_user_id,
            "scheduled_for": _utc_datetime_payload(activity.scheduled_for),
            "created_at": activity.created_at.isoformat(),
            "is_overdue": (
                activity.scheduled_for is not None
                and activity.scheduled_for < datetime.now(timezone.utc).replace(tzinfo=None)
            ),
        }
        for activity in db.scalars(
            select(LeadActivity)
            .where(LeadActivity.lead_id == lead_id)
            .order_by(LeadActivity.created_at.desc(), LeadActivity.id.desc())
        ).all()
    ]


@router.post("/{lead_id}/activities", status_code=status.HTTP_201_CREATED)
def create_lead_activity(
    lead_id: int,
    request: LeadActivityCreate,
    admissions_user: User = Depends(require_roles("admin", "admissions")),
    db: Session = Depends(get_db),
) -> dict:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ tư vấn.")
    scheduled_for = request.scheduled_for
    if scheduled_for is not None:
        if scheduled_for.tzinfo is not None:
            scheduled_for = scheduled_for.astimezone(timezone.utc).replace(tzinfo=None)
        if scheduled_for <= datetime.now(timezone.utc).replace(tzinfo=None):
            raise HTTPException(
                status_code=422, detail="Thời gian gọi lại phải ở trong tương lai."
            )
    activity = LeadActivity(
        lead_id=lead.id,
        actor_user_id=admissions_user.id,
        message=request.message,
        activity_type=request.activity_type,
        scheduled_for=scheduled_for,
    )
    lead.updated_by_id = admissions_user.id
    db.add(activity)
    if scheduled_for is not None:
        db.add(
            Notification(
                user_id=lead.assigned_to_user_id or admissions_user.id,
                lead_id=lead.id,
                title="Đã đặt lịch gọi lại",
                message=f"{lead.full_name}: {request.message}",
                scheduled_for=scheduled_for,
            )
        )
    db.commit()
    db.refresh(activity)
    return {
        "id": activity.id,
        "message": activity.message,
        "activity_type": activity.activity_type,
        "actor_user_id": activity.actor_user_id,
        "scheduled_for": _utc_datetime_payload(activity.scheduled_for),
        "created_at": activity.created_at.isoformat(),
        "is_overdue": False,
    }


@router.post("/{lead_id}/convert")
def convert_lead_to_student(
    lead_id: int,
    admissions_user: User = Depends(require_roles("admin", "admissions")),
    db: Session = Depends(get_db),
) -> dict:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ tư vấn.")
    if lead.converted_user_id is not None:
        student = db.get(User, lead.converted_user_id)
        if student is not None:
            return {
                "created": False,
                "student": {
                    "id": student.id,
                    "username": student.username,
                    "student_code": student.student_code,
                    "email": student.email,
                    "full_name": student.full_name,
                    "must_change_password": student.must_change_password,
                },
            }
    if lead.status not in {"closed", "qualified"}:
        raise HTTPException(
            status_code=409, detail="Chỉ hồ sơ đã chốt mới được chuyển thành học viên."
        )
    if db.scalar(select(User.id).where(func.lower(User.email) == lead.email.lower())):
        raise HTTPException(
            status_code=409,
            detail="Email này đã được sử dụng. Hãy xử lý hồ sơ trùng trước khi chuyển đổi.",
        )
    student_role = db.scalar(select(Role).where(Role.code == "student"))
    if student_role is None:
        raise HTTPException(
            status_code=503,
            detail="Danh mục vai trò chưa được khởi tạo. Chạy seed hệ thống trước.",
        )

    year_prefix = f"SV{datetime.now().year}"
    sequence = 1
    while db.scalar(
        select(User.id).where(
            User.student_code == f"{year_prefix}{sequence:04d}"
        )
    ):
        sequence += 1
    student_code = f"{year_prefix}{sequence:04d}"
    student = User(
        username=student_code.lower(),
        email=lead.email.lower(),
        full_name=lead.full_name,
        phone=lead.phone,
        student_code=student_code,
        hashed_password=hash_password(DEFAULT_ACCOUNT_PASSWORD),
        must_change_password=True,
        roles=[student_role],
    )
    db.add(student)
    db.flush()
    db.add(
        StudentProfile(
            user_id=student.id,
            lead_id=lead.id,
            study_program=lead.interested_program,
        )
    )
    lead.converted_user_id = student.id
    lead.status = "enrolled"
    lead.updated_by_id = admissions_user.id
    db.add(
        LeadActivity(
            lead_id=lead.id,
            actor_user_id=admissions_user.id,
            message=f"Đã chuyển thành học viên {student_code}. Tài khoản yêu cầu đổi mật khẩu lần đầu.",
        )
    )
    db.commit()
    return {
        "created": True,
        "initial_password": DEFAULT_ACCOUNT_PASSWORD,
        "student": {
            "id": student.id,
            "username": student.username,
            "student_code": student.student_code,
            "email": student.email,
            "full_name": student.full_name,
            "must_change_password": student.must_change_password,
        },
    }
