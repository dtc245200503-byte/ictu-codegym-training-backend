"""Durable, deduplicated class reminders and independent SMTP worker entrypoint."""
from __future__ import annotations

import argparse
import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.attendance import AttendanceSession
from app.models.notification import Notification, NotificationOutbox
from app.models.student_profile import StudentProfile
from app.models.training_class import ClassEnrollment, TrainingClass
from app.models.user import User
from app.services.attendance_summary import HO_CHI_MINH
from app.services.email_service import send_notification_email

logger = logging.getLogger(__name__)


def _smtp_ready() -> bool:
    return all((settings.MAIL_USERNAME, settings.MAIL_PASSWORD, settings.MAIL_FROM, settings.MAIL_SERVER))


def _send(recipient: str, title: str, body: str) -> None:
    asyncio.run(send_notification_email(recipient, title=title, body=body))


def process_class_reminders(
    db: Session,
    *,
    now: datetime | None = None,
    email_sender: Callable[[str, str, str], None] | None = None,
    send_emails: bool | None = None,
) -> dict[str, int]:
    now = (now or datetime.now(HO_CHI_MINH)).astimezone(HO_CHI_MINH)
    now_naive = now.replace(tzinfo=None)
    lead = timedelta(hours=max(1, settings.CLASS_REMINDER_LEAD_HOURS))
    report = {"created": 0, "sent": 0, "failed": 0, "pending": 0, "skipped_opt_out": 0}
    rows = db.execute(
        select(AttendanceSession, TrainingClass, ClassEnrollment, User, StudentProfile)
        .join(TrainingClass, TrainingClass.id == AttendanceSession.class_id)
        .join(ClassEnrollment, ClassEnrollment.class_id == TrainingClass.id)
        .join(User, User.id == ClassEnrollment.student_id)
        .outerjoin(StudentProfile, StudentProfile.user_id == User.id)
        .where(AttendanceSession.is_cancelled.is_(False), ClassEnrollment.enrollment_status == "active")
    ).all()
    for session, training_class, enrollment, student, profile in rows:
        starts_at = datetime.combine(session.session_date, session.start_time, tzinfo=HO_CHI_MINH)
        reminder_at = starts_at - lead
        if starts_at <= now or reminder_at > now:
            continue
        if profile is not None and profile.class_reminder_opt_out:
            report["skipped_opt_out"] += 1
            continue
        dedupe = f"class-session:{session.id}:student:{student.id}:reminder"
        if db.scalar(select(NotificationOutbox.id).where(NotificationOutbox.dedupe_key == dedupe)):
            continue
        notification = Notification(
            user_id=student.id, class_id=training_class.id,
            title="Nhắc lịch học sắp tới",
            message=f"Lớp {training_class.code}; buổi học {starts_at.isoformat()} tại {session.room or training_class.room}.",
            scheduled_for=reminder_at.replace(tzinfo=None),
        )
        db.add(notification)
        db.flush()
        db.add(NotificationOutbox(
            notification_id=notification.id, dedupe_key=dedupe,
            session_id=session.id,
            recipient_email=student.email, status="pending", attempt_count=0,
        ))
        try:
            db.commit()
            report["created"] += 1
        except IntegrityError:
            db.rollback()

    enabled = (
        settings.NOTIFICATION_EMAIL_ENABLED and _smtp_ready()
        if send_emails is None
        else bool(send_emails and (email_sender is not None or _smtp_ready()))
    )
    sender = email_sender or _send
    outbox_rows = db.execute(
        select(NotificationOutbox, Notification)
        .join(Notification, Notification.id == NotificationOutbox.notification_id)
        .where(NotificationOutbox.status == "pending", NotificationOutbox.attempt_count < 3)
        .order_by(NotificationOutbox.id)
    ).all()
    for outbox, notification in outbox_rows:
        if outbox.session_id is not None:
            session = db.get(AttendanceSession, outbox.session_id)
            if session is None or session.is_cancelled:
                db.delete(notification)
                db.commit()
                continue
        if not enabled:
            report["pending"] += 1
            continue
        try:
            sender(outbox.recipient_email, notification.title, notification.message)
            outbox.attempt_count += 1
            outbox.status = "sent"
            outbox.sent_at = now_naive
            outbox.last_error = None
            report["sent"] += 1
        except Exception as exc:
            outbox.attempt_count += 1
            outbox.last_error = str(exc)[:500]
            outbox.status = "failed" if outbox.attempt_count >= 3 else "pending"
            report["failed"] += 1
            logger.warning("Notification delivery failed for outbox id %s", outbox.id)
        db.commit()
    return report


def run_once() -> dict[str, int]:
    db = SessionLocal()
    try:
        return process_class_reminders(db)
    finally:
        db.close()


def run_worker() -> None:
    if not settings.ENABLE_NOTIFICATION_WORKER:
        raise RuntimeError("Set ENABLE_NOTIFICATION_WORKER=true to start the notification scheduler.")
    while True:
        run_once()
        time.sleep(60)


def main() -> None:
    parser = argparse.ArgumentParser(description="ICTU class reminder notification worker")
    parser.add_argument("--loop", action="store_true", help="run the independent scheduler/worker")
    args = parser.parse_args()
    if args.loop:
        run_worker()
    else:
        print(run_once())


if __name__ == "__main__":
    main()
