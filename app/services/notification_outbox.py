"""Utilities for atomically pairing in-app notices with SMTP outbox work."""
from sqlalchemy.orm import Session

from app.models.notification import Notification, NotificationOutbox
from app.models.user import User


def enqueue_notification_email(db: Session, notification: Notification, recipient: User | None = None) -> None:
    """Queue email without performing network I/O in the originating request."""
    db.flush()
    recipient = recipient or db.get(User, notification.user_id)
    if recipient is None:
        return
    dedupe_key = f"notification:{notification.id}"
    db.add(NotificationOutbox(
        notification_id=notification.id,
        dedupe_key=dedupe_key,
        recipient_email=recipient.email,
        status="pending",
        attempt_count=0,
    ))
