from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Table, Column, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


announcement_classes = Table(
    "announcement_classes",
    Base.metadata,
    Column("announcement_id", ForeignKey("announcements.id", ondelete="CASCADE"), primary_key=True),
    Column("class_id", ForeignKey("training_classes.id", ondelete="CASCADE"), primary_key=True),
)


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.current_timestamp(), nullable=False)


class SessionLearningLink(Base):
    __tablename__ = "session_learning_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("attendance_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.current_timestamp(), nullable=False)
