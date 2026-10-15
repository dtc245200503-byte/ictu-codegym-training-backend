from collections import Counter
from datetime import timezone
from app.crud import consultations
from fastapi import HTTPException
from sqlalchemy import func
from app.core.permissions import user_has_permission
from app.models.user import User
from app.models.role import Role
from app.models.user_role import UserRole
from app.models.lead_assignment import LeadAssignmentHistory
from app.models.consultation import ConsultationLead
from app.crud.consultations import fingerprint, initialize_bucket, locked_bucket, utcnow
from app.schemas.profile import UpdateProfileRequest


def phone_variants(phone):
    return [phone, "+84" + phone[1:]]


def canonical_phone(phone):
    try:
        return UpdateProfileRequest.validate_phone(phone)
    except ValueError:
        return phone.strip()


def user_roles(db, user):
    return {row[0] for row in db.query(Role.role_name).join(UserRole, UserRole.role_id == Role.role_id).filter(UserRole.user_id == user.user_id)}


def active_leads(db, viewer=None):
    query = db.query(ConsultationLead).filter(ConsultationLead.deleted_at.is_(None))
    if viewer is not None and not user_roles(db, viewer) & {"ADMIN", "TRAINING_MANAGER"}:
        query = query.filter(ConsultationLead.assignee_id == viewer.user_id)
    return query


def find_lead(db, lead_id, lock=False, viewer=None):
    query = active_leads(db, viewer).filter(ConsultationLead.lead_id == lead_id)
    if lock:
        query = query.populate_existing().with_for_update()
    lead = query.first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy khách hàng tiềm năng.")
    return lead


def check_duplicates(db, phone, exclude_lead_id=None, lock=False, viewer=None):
    query = active_leads(db).filter(func.trim(ConsultationLead.phone).in_(phone_variants(phone)))
    if exclude_lead_id is not None:
        query = query.filter(ConsultationLead.lead_id != exclude_lead_id)
    query = query.order_by(ConsultationLead.lead_id)
    if lock:
        query = query.with_for_update()  # Current read after taking the phone lock on MySQL.
    matches = query.all()
    scoped = viewer is not None and not user_roles(db, viewer) & {"ADMIN", "TRAINING_MANAGER"}
    visible = [lead for lead in matches if not scoped or lead.assignee_id == viewer.user_id]
    # Retain the duplicate warning without exposing another advisor's contact data or count.
    return {"total": len(visible), "hidden_match": len(visible) != len(matches), "duplicates": [{"lead_id": lead.lead_id, "full_name": lead.full_name,
        "phone": lead.phone, "source": lead.source, "interest": lead.interest} for lead in visible[:5]]}


def serialize_leads(db, leads, viewer=None):
    if not leads:
        return []
    variants = {value for lead in leads for value in phone_variants(canonical_phone(lead.phone))}
    counts = Counter(canonical_phone(row[0]) for row in active_leads(db, viewer).with_entities(ConsultationLead.phone).filter(
        func.trim(ConsultationLead.phone).in_(variants)).all())
    users = {user.user_id: user.full_name for user in db.query(User).filter(User.user_id.in_({lead.assignee_id for lead in leads if lead.assignee_id}))}
    now = consultations.utcnow()
    return [{"lead_id": lead.lead_id, "full_name": lead.full_name, "phone": lead.phone, "email": lead.email,
        "source": lead.source, "interest": lead.interest, "message": lead.message, "status": lead.status,
        "created_at": lead.created_at, "duplicate_count": max(0, counts[canonical_phone(lead.phone)] - 1),
        "assignee_id": lead.assignee_id, "assignee_name": users.get(lead.assignee_id),
        "next_callback_at": lead.next_callback_at.replace(tzinfo=timezone.utc) if lead.next_callback_at else None,
        "callback_overdue": lead.next_callback_at is not None and lead.next_callback_at < now} for lead in leads]


def save_lead(db, data, lead_id=None, viewer=None):
    key = fingerprint(f"phone:{data.phone}")
    initialize_bucket(db, key, utcnow())
    db.commit()  # Start the consistent-read snapshot after taking the phone lock.
    locked_bucket(db, key)  # Share the serialization lock with public submissions.
    lead = find_lead(db, lead_id, lock=True, viewer=viewer) if lead_id is not None else None
    duplicates = check_duplicates(db, data.phone, lead_id, viewer=viewer)
    if (duplicates["total"] or duplicates["hidden_match"]) and not data.confirm_duplicate:
        raise HTTPException(status_code=409, detail={"message": "Số điện thoại trùng với khách hàng tiềm năng đã có. Vui lòng kiểm tra và xác nhận trước khi lưu.", **duplicates})
    if lead is None:
        auto_assign = viewer is not None and not user_roles(db, viewer) & {"ADMIN", "TRAINING_MANAGER"}
        lead = ConsultationLead(status="new", created_at=utcnow())
        db.add(lead)
        if auto_assign:
            # A manually created advisor lead is automatically owned by its creator.
            lead.assignee_id = viewer.user_id
    lead.full_name, lead.phone, lead.email = data.full_name, data.phone, str(data.email) if data.email else None
    lead.source, lead.interest = data.source, data.interest
    if lead_id is None and lead.assignee_id is not None:
        db.flush()
        record_assignment(db, lead, None, viewer, viewer, "Tự động giao lead do tư vấn viên tạo.")
    # Editing contact details preserves the public message, status and created time.
    db.commit(); db.refresh(lead)
    return serialize_leads(db, [lead], viewer)[0]


def record_assignment(db, lead, previous, target, actor, note):
    db.add(LeadAssignmentHistory(lead_id=lead.lead_id, from_assignee_id=previous.user_id if previous else None,
        from_assignee_name=previous.full_name if previous else None, to_assignee_id=target.user_id,
        to_assignee_name=target.full_name, actor_id=actor.user_id, actor_name=actor.full_name,
        note=note or None, created_at=utcnow()))


def eligible_advisors(db):
    return db.query(User).join(UserRole, UserRole.user_id == User.user_id).join(Role, Role.role_id == UserRole.role_id).filter(
        Role.role_name == "ADMISSIONS", User.is_locked.is_(False),
        (User.locked_until.is_(None)) | (User.locked_until <= utcnow()))


def assign_leads(db, data, actor):
    # Serialize on the recipient first, then lead IDs in a stable order. One transaction
    # covers every assignment and its history, including rejection of a missing lead.
    target = eligible_advisors(db).filter(User.user_id == data.assignee_id).populate_existing().with_for_update().first()
    if target is None or not user_has_permission(data.assignee_id, "LEAD_MANAGE", db):
        raise HTTPException(status_code=400, detail="Người nhận phải là tư vấn viên đang hoạt động và có quyền quản lý lead.")
    leads = active_leads(db).filter(ConsultationLead.lead_id.in_(data.lead_ids)).order_by(ConsultationLead.lead_id).populate_existing().with_for_update().all()
    if len(leads) != len(data.lead_ids):
        raise HTTPException(status_code=404, detail="Có lead không tồn tại hoặc đã bị xóa. Chưa phân công lead nào.")
    changed = 0
    for lead in leads:
        if lead.assignee_id == target.user_id:
            continue
        previous = db.get(User, lead.assignee_id) if lead.assignee_id else None
        record_assignment(db, lead, previous, target, actor, data.note)
        lead.assignee_id = target.user_id
        changed += 1
    db.commit()
    return {"changed": changed, "unchanged": len(leads) - changed, "message": f"Đã phân công {changed} lead; {len(leads) - changed} lead đã thuộc tư vấn viên này."}
