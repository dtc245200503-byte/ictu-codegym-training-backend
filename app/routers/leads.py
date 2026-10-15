from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import date, datetime, time, timedelta
import re
from typing import Literal
from app.crud import consultations
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.core.permissions import require_permission
from app.database import get_db
from app.models.consultation import ConsultationLead
from app.models.role import Role
from app.models.user import User
from app.models.user_role import UserRole
from app.crud.consultations import utcnow
from app.crud.leads import active_leads, check_duplicates, find_lead, save_lead, serialize_leads
from app.schemas.lead import DuplicateCheck, LeadListResponse, LeadRequest, LeadResponse
from app.schemas.profile import UpdateProfileRequest
from app.crud.leads import assign_leads, eligible_advisors
from app.core.permissions import user_has_permission
from app.models.lead_assignment import LeadAssignmentHistory
from app.schemas.lead import AssignLeadsRequest, AssignmentResponse, AdvisorResponse, AssignmentHistoryList
from app.schemas.lead import LeadStatus, LeadFilterOptions
from app.crud.leads import user_roles

router = APIRouter(prefix="/api/leads", tags=["Khách hàng tiềm năng"], dependencies=[Depends(require_permission("LEAD_MANAGE"))])


def require_training_manager(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    role = db.query(UserRole).join(Role, Role.role_id == UserRole.role_id).filter(
        UserRole.user_id == user.user_id, Role.role_name == "TRAINING_MANAGER").first()
    if role is None:
        raise HTTPException(status_code=403, detail="Thao tác này chỉ dành cho Quản lý đào tạo.")
    return user


@router.get("", response_model=LeadListResponse)
def list_leads(search: str = Query("", max_length=100), source: str = Query("", max_length=100),
               page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               assignee_id: int | None = Query(None, ge=0), status: LeadStatus | None = Query(None),
               callback_status: Literal["overdue", "upcoming", "none"] | None = Query(None),
               date_from: date | None = Query(None), date_to: date | None = Query(None),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="Ngày bắt đầu không được sau ngày kết thúc.")
    query = active_leads(db, user)
    if callback_status == "overdue":
        query = query.filter(ConsultationLead.next_callback_at < consultations.utcnow())
    elif callback_status == "upcoming":
        query = query.filter(ConsultationLead.next_callback_at >= consultations.utcnow())
    elif callback_status == "none":
        query = query.filter(ConsultationLead.next_callback_at.is_(None))
    if status is not None:
        query = query.filter(ConsultationLead.status == status)
    # Stored timestamps are UTC; calendar dates in this Vietnamese UI use UTC+7.
    # Use a half-open interval to include the whole last day without rounding errors.
    try:
        if date_from:
            start = datetime.combine(date_from, time.min) - timedelta(hours=7)
            query = query.filter(ConsultationLead.created_at >= start)
        if date_to:
            end = datetime.combine(date_to, time.min) - timedelta(hours=7) + timedelta(days=1)
            query = query.filter(ConsultationLead.created_at < end)
    except OverflowError:
        raise HTTPException(status_code=400, detail="Khoảng ngày nằm ngoài giới hạn hỗ trợ.")
    if assignee_id is not None:
        query = query.filter(ConsultationLead.assignee_id.is_(None) if assignee_id == 0 else ConsultationLead.assignee_id == assignee_id)
    if search.strip():
        term = search.strip().lower()
        filters = [func.lower(column).contains(term, autoescape=True) for column in [ConsultationLead.full_name,
            ConsultationLead.phone, ConsultationLead.email, ConsultationLead.source, ConsultationLead.interest]]
        compact_phone = re.sub(r"[\s().-]", "", term)
        if re.fullmatch(r"(?:\+84|0)\d+", compact_phone):
            local_phone = "0" + compact_phone[3:] if compact_phone.startswith("+84") else compact_phone
            filters.extend(func.trim(ConsultationLead.phone).contains(value, autoescape=True)
                           for value in [local_phone, "+84" + local_phone[1:]])
        try:
            phone = UpdateProfileRequest.validate_phone(term)
            if phone:
                filters.append(func.trim(ConsultationLead.phone).in_([phone, "+84"+phone[1:]]))
        except ValueError:
            pass
        query = query.filter(or_(*filters))
    if source.strip():
        query = query.filter(ConsultationLead.source == source.strip())
    total = query.count()
    items = query.order_by(ConsultationLead.created_at.desc(), ConsultationLead.lead_id.desc()).offset((page-1)*page_size).limit(page_size).all()
    return {"items": serialize_leads(db, items, user), "total": total, "page": page, "page_size": page_size}


@router.get("/filter-options", response_model=LeadFilterOptions)
def filter_options(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    visible = active_leads(db, user)
    sources = [row[0] for row in visible.with_entities(ConsultationLead.source).distinct().order_by(ConsultationLead.source)]
    ids = visible.with_entities(ConsultationLead.assignee_id).filter(ConsultationLead.assignee_id.is_not(None))
    assignees = db.query(User).filter(User.user_id.in_(ids)).order_by(User.full_name, User.user_id).all()
    return {"sources": sources, "assignees": [{"user_id": u.user_id, "full_name": u.full_name} for u in assignees],
            "can_view_all": bool(user_roles(db, user) & {"ADMIN", "TRAINING_MANAGER"})}


@router.get("/check-phone", response_model=DuplicateCheck)
def duplicate_phone(phone: str = Query(min_length=1, max_length=20), exclude_lead_id: int | None = Query(None, gt=0), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        normalized = UpdateProfileRequest.validate_phone(phone)
        if not normalized:
            raise ValueError()
    except ValueError:
        raise HTTPException(status_code=400, detail="Số điện thoại Việt Nam không hợp lệ.")
    if exclude_lead_id is not None:
        find_lead(db, exclude_lead_id, viewer=user)
    return check_duplicates(db, normalized, exclude_lead_id, viewer=user)


@router.get("/advisors", response_model=list[AdvisorResponse])
def list_advisors(user: User = Depends(require_training_manager), db: Session = Depends(get_db)):
    return [{"user_id": advisor.user_id, "full_name": advisor.full_name, "email": advisor.email}
            for advisor in eligible_advisors(db).order_by(User.full_name, User.user_id).all()
            if user_has_permission(advisor.user_id, "LEAD_MANAGE", db)]


@router.post("/assign", response_model=AssignmentResponse)
def assign(data: AssignLeadsRequest, user: User = Depends(require_training_manager), db: Session = Depends(get_db)):
    return assign_leads(db, data, user)


@router.get("/{lead_id}/assignment-history", response_model=AssignmentHistoryList)
def assignment_history(lead_id: int, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    find_lead(db, lead_id, lock=True, viewer=user)
    query = db.query(LeadAssignmentHistory).filter_by(lead_id=lead_id)
    return {"items": query.order_by(LeadAssignmentHistory.history_id.desc()).offset((page-1)*page_size).limit(page_size).all(),
            "total": query.count(), "page": page, "page_size": page_size}


@router.get("/{lead_id}", response_model=LeadResponse)
def get_lead(lead_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return serialize_leads(db, [find_lead(db, lead_id, lock=True, viewer=user)], user)[0]


@router.post("", response_model=LeadResponse, status_code=201)
def create_lead(data: LeadRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return save_lead(db, data, viewer=user)


@router.put("/{lead_id}", response_model=LeadResponse)
def update_lead(lead_id: int, data: LeadRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return save_lead(db, data, lead_id, viewer=user)


@router.delete("/{lead_id}", dependencies=[Depends(require_training_manager)])
def delete_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = find_lead(db, lead_id, lock=True)
    lead.deleted_at = utcnow()
    db.commit()
    return {"message": "Đã xóa khách hàng tiềm năng khỏi danh sách.", "lead_id": lead_id}
