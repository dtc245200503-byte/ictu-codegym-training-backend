from datetime import timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.core.permissions import require_permission
from app.crud import consultations
from app.crud.leads import find_lead, serialize_leads
from app.database import get_db
from app.models.lead_care import LeadCareLog
from app.models.user import User
from app.schemas.lead import LeadResponse
from app.schemas.lead_care import CallbackRequest, CareLogRequest, CareLogResponse, CareLogList

router = APIRouter(prefix="/api/leads", tags=["Chăm sóc khách hàng"],
                   dependencies=[Depends(require_permission("LEAD_MANAGE"))])


def utc_naive(value):
    try:
        result = value.astimezone(timezone.utc).replace(tzinfo=None) if value is not None else None
    except (OverflowError, ValueError):
        raise HTTPException(400, "Thời điểm nằm ngoài giới hạn hỗ trợ.")
    # MySQL DATETIME supports years 1000–9999, unlike Python's datetime.
    if result is not None and result.year < 1000:
        raise HTTPException(400, "Thời điểm nằm ngoài giới hạn hỗ trợ.")
    return result


def validate_callback(value, now):
    callback = utc_naive(value)
    if callback is not None and callback <= now:
        raise HTTPException(400, "Ngày nhắc gọi lại phải ở tương lai.")
    return callback


@router.get("/{lead_id}/care-logs", response_model=CareLogList)
def care_logs(lead_id: int, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    lead = find_lead(db, lead_id, lock=True, viewer=user)
    query = db.query(LeadCareLog).filter_by(lead_id=lead_id)
    return {"items": query.order_by(LeadCareLog.occurred_at.desc(), LeadCareLog.log_id.desc())
            .offset((page - 1) * page_size).limit(page_size).all(),
            "total": query.count(), "page": page, "page_size": page_size,
            "next_callback_at": lead.next_callback_at,
            "callback_overdue": lead.next_callback_at is not None and lead.next_callback_at < consultations.utcnow()}


@router.post("/{lead_id}/care-logs", response_model=CareLogResponse, status_code=201)
def add_care_log(lead_id: int, data: CareLogRequest,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    lead = find_lead(db, lead_id, lock=True, viewer=user)
    now = consultations.utcnow()
    occurred = utc_naive(data.occurred_at)
    if occurred > now:
        raise HTTPException(400, "Thời điểm tương tác không được ở tương lai.")
    callback = validate_callback(data.next_callback_at, now)
    if data.callback_action == "schedule":
        lead.next_callback_at = callback
    elif data.callback_action == "complete":
        lead.next_callback_at = None
    log = LeadCareLog(lead_id=lead_id, interaction_type=data.interaction_type, content=data.content,
                     occurred_at=occurred, created_at=now, actor_id=user.user_id, actor_name=user.full_name,
                     callback_action=data.callback_action, next_callback_at=callback)
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


@router.put("/{lead_id}/callback", response_model=LeadResponse)
def update_callback(lead_id: int, data: CallbackRequest,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    lead = find_lead(db, lead_id, lock=True, viewer=user)
    lead.next_callback_at = validate_callback(data.next_callback_at, consultations.utcnow())
    db.commit()
    db.refresh(lead)
    return serialize_leads(db, [lead], user)[0]
