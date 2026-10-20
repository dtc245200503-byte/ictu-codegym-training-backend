from sqlalchemy import Column, ForeignKey, Integer, String, Text, JSON, UniqueConstraint
from app.database import Base
from app.models.consultation import PRECISE_DATETIME


class SessionChange(Base):
    __tablename__ = "session_changes"
    change_id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    operation = Column(String(20), nullable=False)
    reason = Column(String(1000), nullable=True)
    before = Column(JSON, nullable=False)
    after = Column(JSON, nullable=False)
    actor_id = Column(Integer, nullable=False)
    actor_name = Column(String(100), nullable=False)
    created_at = Column(PRECISE_DATETIME, nullable=False)
    __table_args__ = (UniqueConstraint("session_id", "version", name="uq_session_change_version"),)


class Notification(Base):
    __tablename__ = "notifications"
    notification_id = Column(Integer, primary_key=True, autoincrement=True)
    recipient_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    change_id = Column(Integer, ForeignKey("session_changes.change_id"), nullable=False)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(PRECISE_DATETIME, nullable=False)
    read_at = Column(PRECISE_DATETIME, nullable=True)
    __table_args__ = (UniqueConstraint("recipient_id", "change_id", name="uq_notification_recipient_change"),)
