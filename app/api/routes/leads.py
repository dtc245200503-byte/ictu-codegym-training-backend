from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, selectinload

from app.api.dependencies import require_roles
from app.api.pagination import set_pagination_headers
from app.core.security import hash_password
from app.core.config import settings
from app.db.session import get_db
from app.models.lead import Lead, LeadActivity, LeadAssignmentHistory, LeadSubmissionRateLimit
from app.models.notification import Notification
from app.models.role import Role
from app.models.student_profile import StudentProfile
from app.models.user import User
from app.schemas.leads import LeadActivityCreate, LeadBulkAssignment, LeadCreate, LeadManualCreate, LeadUpdate
from app.services.email_service import send_account_credentials_email

router = APIRouter(prefix="/leads", tags=["Admissions leads"])
MANAGEMENT_ROLES = {"admin", "manager"}
LEAD_FUNNEL_STATUSES = ("new", "contacted", "consulting", "trial_scheduled", "closed", "rejected", "enrolled")


def _assert_lead_scope(lead: Lead, user: User) -> None:
    roles = {role.code for role in user.roles}
    if roles.intersection(MANAGEMENT_ROLES) or lead.assigned_to_user_id == user.id:
        return
    raise HTTPException(status_code=404, detail="Lead not found.")


def _consume_public_submission_limit(request: Request, db: Session) -> None:
    limit = max(1, settings.LEAD_SUBMISSION_LIMIT)
    window_seconds = max(1, settings.LEAD_SUBMISSION_WINDOW_SECONDS)
    host = request.client.host if request.client is not None else "unknown"
    fingerprint = hmac.new(settings.JWT_SECRET_KEY.encode(), host.encode(), hashlib.sha256).hexdigest()
    now = datetime.now(timezone.utc)
    epoch = int(now.timestamp())
    bucket_epoch = epoch - epoch % window_seconds
    window_started_at = datetime.fromtimestamp(bucket_epoch, timezone.utc).replace(tzinfo=None)
    values = {"client_fingerprint": fingerprint, "window_started_at": window_started_at, "attempts": 1}
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        statement = sqlite_insert(LeadSubmissionRateLimit).values(**values).on_conflict_do_update(
            index_elements=[LeadSubmissionRateLimit.client_fingerprint, LeadSubmissionRateLimit.window_started_at],
            set_={"attempts": LeadSubmissionRateLimit.attempts + 1},
        )
    elif dialect == "mysql":
        statement = mysql_insert(LeadSubmissionRateLimit).values(**values).on_duplicate_key_update(
            attempts=LeadSubmissionRateLimit.attempts + 1,
        )
    else:
        row = db.get(LeadSubmissionRateLimit, (fingerprint, window_started_at))
        if row is None:
            db.add(LeadSubmissionRateLimit(**values))
        else:
            row.attempts += 1
    if dialect in {"sqlite", "mysql"}:
        db.execute(statement)
    db.execute(delete(LeadSubmissionRateLimit).where(
        LeadSubmissionRateLimit.window_started_at < window_started_at - timedelta(days=2)
    ))
    db.flush()
    row = db.get(LeadSubmissionRateLimit, (fingerprint, window_started_at))
    attempts = row.attempts if row else limit + 1
    db.commit()
    if attempts > limit:
        retry_after = max(1, window_seconds - (epoch - bucket_epoch))
        raise HTTPException(status_code=429, detail="Too many consultation requests. Try again later.",
            headers={"Retry-After": str(retry_after)})


def _utc_datetime_payload(value: datetime | None) -> str | None:
    return f"{value.isoformat()}Z" if value is not None else None


def _lead_payload(lead: Lead) -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    activities = getattr(lead, "activities", [])
    has_overdue = any(
        act.scheduled_for is not None and act.scheduled_for < now
        for act in activities
    )
    future_callbacks = [
        act.scheduled_for
        for act in activities
        if act.scheduled_for is not None and act.scheduled_for >= now
    ]
    next_callback = min(future_callbacks) if future_callbacks else None
    return {
        "id": lead.id,
        "full_name": lead.full_name,
        "email": lead.email,
        "phone": lead.phone,
        "source": lead.source,
        "interested_program": lead.interested_program,
        "message": lead.message,
        "status": lead.status,
        "notes": lead.notes,
        "rejection_reason": lead.rejection_reason,
        "assigned_to_user_id": lead.assigned_to_user_id,
        "converted_user_id": lead.converted_user_id,
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        "updated_at": lead.updated_at.isoformat() if lead.updated_at else None,
        "has_overdue_callback": has_overdue,
        "next_callback_at": next_callback.isoformat() if next_callback else None,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_lead(request: LeadCreate, request_context: Request, db: Session = Depends(get_db)) -> dict:
    _consume_public_submission_limit(request_context, db)
    duplicate_phone_warning = db.scalar(select(Lead.id).where(Lead.phone == request.phone).limit(1)) is not None
    lead = Lead(
        full_name=request.full_name,
        email=str(request.email),
        phone=request.phone,
        source="website",
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
    return {**_lead_payload(lead), "duplicate_phone_warning": duplicate_phone_warning}


@router.post("/managed", status_code=status.HTTP_201_CREATED)
def create_managed_lead(
    request: LeadManualCreate,
    actor: User = Depends(require_roles("admin", "manager", "admissions")),
    db: Session = Depends(get_db),
) -> dict:
    duplicate_phone_warning = db.scalar(select(Lead.id).where(Lead.phone == request.phone).limit(1)) is not None
    lead = Lead(
        full_name=request.full_name, email=str(request.email), phone=request.phone,
        interested_program=request.interested_program, message=request.message, source=request.source,
        assigned_to_user_id=actor.id if "admissions" in {role.code for role in actor.roles} else None,
        updated_by_id=actor.id,
    )
    db.add(lead)
    db.flush()
    db.add(LeadActivity(lead_id=lead.id, actor_user_id=actor.id,
        message="Lead created by admissions staff.", activity_type="other"))
    db.commit()
    db.refresh(lead)
    return {**_lead_payload(lead), "duplicate_phone_warning": duplicate_phone_warning}


@router.get("/funnel-summary")
def lead_funnel_summary(
    current_user: User = Depends(require_roles("admin", "manager", "admissions")),
    db: Session = Depends(get_db),
) -> dict:
    query = select(Lead.status, func.count(Lead.id)).where(Lead.status.in_(LEAD_FUNNEL_STATUSES))
    if not ({role.code for role in current_user.roles} & MANAGEMENT_ROLES):
        query = query.where(Lead.assigned_to_user_id == current_user.id)
    counts = {lead_status: 0 for lead_status in LEAD_FUNNEL_STATUSES}
    counts.update({lead_status: count for lead_status, count in db.execute(query.group_by(Lead.status)).all()})
    return {"counts": counts, "total": sum(counts.values())}


@router.get("")
def list_leads(
    q: str = Query(default="", max_length=150),
    lead_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(new|contacted|consulting|trial_scheduled|closed|rejected|enrolled|qualified)$",
    ),
    source: str | None = Query(default=None, max_length=80),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    assigned_to_user_id: int | None = Query(default=None, ge=1),
    sort_by: str = Query(default="created_at", pattern="^(created_at|updated_at|full_name|status)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    offset: int | None = Query(default=None, ge=0),
    limit: int | None = Query(default=None, ge=1, le=100),
    current_user: User = Depends(require_roles("admin", "manager", "admissions")),
    db: Session = Depends(get_db),
) -> dict:
    if created_from and created_to and created_from > created_to:
        raise HTTPException(status_code=422, detail="created_from must not be after created_to.")
    query = select(Lead).options(selectinload(Lead.activities))
    if lead_status:
        query = query.where(Lead.status == lead_status)
    if source and source.strip():
        query = query.where(Lead.source == source.strip())
    if not ({role.code for role in current_user.roles} & MANAGEMENT_ROLES):
        query = query.where(or_(Lead.assigned_to_user_id.is_(None), Lead.assigned_to_user_id == current_user.id))
        if assigned_to_user_id is not None and assigned_to_user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Cannot filter leads assigned to another consultant.")
    if assigned_to_user_id is not None:
        query = query.where(Lead.assigned_to_user_id == assigned_to_user_id)
    if created_from is not None:
        query = query.where(Lead.created_at >= created_from)
    if created_to is not None:
        query = query.where(Lead.created_at <= created_to)
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
    actual_page = page
    actual_page_size = page_size
    actual_offset = (page - 1) * page_size
    if offset is not None or limit is not None:
        actual_offset = offset or 0
        actual_page_size = limit or page_size
        actual_page = actual_offset // actual_page_size + 1
    sort_column = {
        "created_at": Lead.created_at,
        "updated_at": Lead.updated_at,
        "full_name": Lead.full_name,
        "status": Lead.status,
    }[sort_by]
    sort_expression = sort_column.asc() if sort_order == "asc" else sort_column.desc()
    leads = db.scalars(
        query.order_by(sort_expression, Lead.id.asc() if sort_order == "asc" else Lead.id.desc())
        .offset(actual_offset)
        .limit(actual_page_size)
    ).all()
    return {
        "items": [_lead_payload(lead) for lead in leads],
        "total": total,
        "offset": actual_offset,
        "limit": actual_page_size,
        "page": actual_page,
        "page_size": actual_page_size,
        "pages": (total + actual_page_size - 1) // actual_page_size,
        "has_previous": actual_page > 1 and total > 0,
        "has_next": actual_page * actual_page_size < total,
    }


@router.get("/{lead_id}")
def get_lead(
    lead_id: int,
    current_user: User = Depends(require_roles("admin", "manager", "admissions")),
    db: Session = Depends(get_db),
) -> dict:
    lead = db.scalar(
        select(Lead)
        .options(selectinload(Lead.activities))
        .where(Lead.id == lead_id)
    )
    if lead is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ tư vấn.")
    _assert_lead_scope(lead, current_user)
    return _lead_payload(lead)


@router.patch("/{lead_id}")
def update_lead(
    lead_id: int,
    request: LeadUpdate,
    admissions_user: User = Depends(require_roles("admin", "manager", "admissions")),
    db: Session = Depends(get_db),
) -> dict:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ tư vấn.")
    if lead.status == "enrolled":
        raise HTTPException(
            status_code=409, detail="Lead đã nhập học và không thể chỉnh sửa."
        )
    _assert_lead_scope(lead, admissions_user)
    updates = request.model_dump(exclude_unset=True)
    previous_notes = lead.notes
    previous_status = lead.status
    previous_assignee = lead.assigned_to_user_id
    roles = {role.code for role in admissions_user.roles}
    if "assigned_to_user_id" in updates and not roles.intersection(MANAGEMENT_ROLES):
        raise HTTPException(status_code=403, detail="Only managers can assign or reassign leads.")
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
    if "assigned_to_user_id" in updates and lead.assigned_to_user_id != previous_assignee:
        db.add(LeadAssignmentHistory(lead_id=lead.id,
            previous_assignee_id=previous_assignee, new_assignee_id=lead.assigned_to_user_id,
            assigned_by_id=admissions_user.id))
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
    elif request.status is not None and request.status != "rejected" and previous_status == "rejected":
        lead.rejection_reason = None
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


@router.post("/bulk-assignment")
def bulk_assign_leads(
    request: LeadBulkAssignment,
    manager: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
) -> dict:
    assignee = db.scalar(select(User).options(selectinload(User.roles)).where(
        User.id == request.assigned_to_user_id,
        User.is_active.is_(True), User.is_locked.is_(False),
    ))
    if assignee is None or "admissions" not in {role.code for role in assignee.roles}:
        raise HTTPException(status_code=422, detail="Assignee must be an active admissions user.")
    leads = db.scalars(select(Lead).where(Lead.id.in_(request.lead_ids)).with_for_update()).all()
    if len(leads) != len(request.lead_ids):
        raise HTTPException(status_code=404, detail="One or more leads were not found; no assignments were changed.")
    changed = 0
    for lead in leads:
        previous = lead.assigned_to_user_id
        if previous == assignee.id:
            continue
        lead.assigned_to_user_id = assignee.id
        lead.updated_by_id = manager.id
        db.add(LeadAssignmentHistory(lead_id=lead.id, previous_assignee_id=previous,
            new_assignee_id=assignee.id, assigned_by_id=manager.id))
        db.add(Notification(user_id=assignee.id, lead_id=lead.id,
            title="Lead assigned to you", message=f"You were assigned lead {lead.full_name}."))
        changed += 1
    db.commit()
    return {"requested_count": len(request.lead_ids), "assigned_count": changed,
            "assigned_to_user_id": assignee.id}


@router.get("/{lead_id}/assignment-history")
def list_lead_assignment_history(
    lead_id: int,
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_roles("admin", "manager", "admissions")),
    db: Session = Depends(get_db),
) -> list[dict]:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found.")
    _assert_lead_scope(lead, current_user)
    query = select(LeadAssignmentHistory).where(LeadAssignmentHistory.lead_id == lead_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(LeadAssignmentHistory.created_at.desc(),
        LeadAssignmentHistory.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    set_pagination_headers(response, total=total, page=page, page_size=page_size)
    return [{"id": item.id, "previous_assignee_id": item.previous_assignee_id,
        "new_assignee_id": item.new_assignee_id, "assigned_by_id": item.assigned_by_id,
        "created_at": item.created_at.isoformat()} for item in rows]


@router.delete("/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lead(
    lead_id: int,
    _manager: User = Depends(require_roles("admin", "manager")),
    db: Session = Depends(get_db),
) -> Response:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found.")
    if lead.converted_user_id is not None or lead.status == "enrolled":
        raise HTTPException(status_code=409, detail="Converted leads are retained for enrollment history.")
    db.delete(lead)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{lead_id}/activities")
def list_lead_activities(
    lead_id: int,
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_roles("admin", "manager", "admissions")),
    db: Session = Depends(get_db),
) -> list[dict]:
    if db.get(Lead, lead_id) is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ tư vấn.")
    _assert_lead_scope(db.get(Lead, lead_id), current_user)
    query = select(LeadActivity).where(LeadActivity.lead_id == lead_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    activities = db.scalars(query.order_by(LeadActivity.created_at.desc(), LeadActivity.id.desc())
        .offset((page - 1) * page_size).limit(page_size)).all()
    set_pagination_headers(response, total=total, page=page, page_size=page_size)
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
        for activity in activities
    ]


@router.post("/{lead_id}/activities", status_code=status.HTTP_201_CREATED)
def create_lead_activity(
    lead_id: int,
    request: LeadActivityCreate,
    admissions_user: User = Depends(require_roles("admin", "manager", "admissions")),
    db: Session = Depends(get_db),
) -> dict:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ tư vấn.")
    _assert_lead_scope(lead, admissions_user)
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
async def convert_lead_to_student(
    lead_id: int,
    admissions_user: User = Depends(require_roles("admin", "manager", "admissions")),
    db: Session = Depends(get_db),
) -> dict:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ tư vấn.")
    _assert_lead_scope(lead, admissions_user)
    if lead.converted_user_id is not None:
        student = db.get(User, lead.converted_user_id)
        if student is not None:
            return {
                "created": False,
                "credential_delivery": "already_created",
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
    temporary_password = secrets.token_urlsafe(18)
    student = User(
        username=student_code.lower(),
        email=lead.email.lower(),
        full_name=lead.full_name,
        phone=lead.phone,
        student_code=student_code,
        hashed_password=hash_password(temporary_password),
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
    mail_configured = all(value.strip() for value in (
        settings.MAIL_USERNAME, settings.MAIL_PASSWORD, settings.MAIL_FROM, settings.MAIL_SERVER,
    ))
    if mail_configured:
        try:
            await send_account_credentials_email(
                student.email, student.full_name, student.username, temporary_password,
            )
        except Exception as error:
            db.rollback()
            raise HTTPException(status_code=502,
                detail="Credential email could not be sent; lead conversion was rolled back.") from error
    db.commit()
    return {
        "created": True,
        "credential_delivery": "email" if mail_configured else "integration_pending",
        "student": {
            "id": student.id,
            "username": student.username,
            "student_code": student.student_code,
            "email": student.email,
            "full_name": student.full_name,
            "must_change_password": student.must_change_password,
        },
    }
