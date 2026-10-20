from app.crud.attendance_risk import refresh
from app.models.attendance_risk import AttendanceRiskNotification
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import String, cast, literal, case
from app.core.auth import get_current_user
from app.crud.attendance import iso, utcnow
from app.database import get_db
from app.models.user import User
from app.models.session_change import Notification
from app.models.absence_request import AbsenceRequest

router = APIRouter(prefix="/api/notifications", tags=["Thông báo"])


def own(db, user):
    return db.query(Notification).filter_by(recipient_id=user.user_id)


def inbox(db, user):
    changes = own(db, user).with_entities(cast(Notification.notification_id, String).label('notification_id'),
        Notification.notification_id.label('sort_id'), Notification.title.label('title'), Notification.message.label('message'),
        Notification.created_at.label('created_at'), Notification.read_at.label('read_at'))
    requests = db.query((literal('absence:') + cast(AbsenceRequest.request_id, String)).label('notification_id'),
        AbsenceRequest.request_id.label('sort_id'), literal('Kết quả đơn xin phép vắng').label('title'),
        (literal('Đơn #') + cast(AbsenceRequest.request_id, String) + literal(': ') +
         case((AbsenceRequest.status == 'approved', 'Đã duyệt — Vắng có phép'), else_='Từ chối') +
         literal('\nLý do: ') + AbsenceRequest.review_reason).label('message'),
        AbsenceRequest.reviewed_at.label('created_at'), AbsenceRequest.notification_read_at.label('read_at')).filter(
        AbsenceRequest.student_id == user.user_id, AbsenceRequest.status.in_(['approved', 'rejected']))
    risks = db.query((literal('risk:') + cast(AttendanceRiskNotification.notification_id, String)).label('notification_id'),
        AttendanceRiskNotification.notification_id.label('sort_id'), AttendanceRiskNotification.title.label('title'),
        AttendanceRiskNotification.message.label('message'), AttendanceRiskNotification.created_at.label('created_at'),
        AttendanceRiskNotification.read_at.label('read_at')).filter(AttendanceRiskNotification.recipient_id == user.user_id)
    combined = changes.union_all(requests, risks).subquery()
    return db.query(combined), combined.c


@router.get("/summary")
def summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    refresh(db)
    query, columns = inbox(db, user)
    return {"unread": query.filter(columns.read_at.is_(None)).count()}


@router.get("")
def listing(page: int = Query(1, ge=1), unread_only: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    refresh(db)
    query, columns = inbox(db, user)
    unread = query.filter(columns.read_at.is_(None)).count()
    if unread_only:
        query = query.filter(columns.read_at.is_(None))
    return {"total": query.count(), "unread": unread,
        "items": [{"notification_id": int(item.notification_id) if item.notification_id.isdigit() else item.notification_id, "title": item.title, "message": item.message,
                   "created_at": iso(item.created_at), "read_at": iso(item.read_at)}
                  for item in query.order_by(columns.created_at.desc(), columns.sort_id.desc(), columns.notification_id).offset((page-1)*20).limit(20)]}


@router.post("/{notification_id}/read")
def mark_read(notification_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if notification_id.startswith('risk:') and notification_id[5:].isdigit():
        query = db.query(AttendanceRiskNotification).filter_by(notification_id=int(notification_id[5:]), recipient_id=user.user_id)
        if query.first() is None:
            raise HTTPException(404, 'Không tìm thấy thông báo.')
        query.filter_by(read_at=None).update({'read_at': utcnow()}, synchronize_session=False)
        db.commit()
        return summary(user, db)
    if notification_id.startswith('absence:') and notification_id[8:].isdigit():
        query = db.query(AbsenceRequest).filter_by(request_id=int(notification_id[8:]), student_id=user.user_id).filter(AbsenceRequest.status.in_(['approved', 'rejected']))
        if query.first() is None:
            raise HTTPException(404, 'Không tìm thấy thông báo.')
        query.filter_by(notification_read_at=None).update({'notification_read_at': utcnow()}, synchronize_session=False)
        db.commit()
        return summary(user, db)
    if not notification_id.isdigit():
        raise HTTPException(422, 'Mã thông báo không hợp lệ.')
    query = own(db, user).filter_by(notification_id=int(notification_id))
    if query.first() is None:
        raise HTTPException(404, "Không tìm thấy thông báo.")
    query.filter_by(read_at=None).update({"read_at": utcnow()}, synchronize_session=False)
    db.commit()
    return summary(user, db)
