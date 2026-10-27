from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Time,
    UniqueConstraint,
    CheckConstraint,
    Numeric,
    func,
)
from sqlalchemy.dialects.mysql import LONGBLOB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class AttendanceSession(Base):
    __tablename__ = "attendance_sessions"
    __table_args__ = (
        UniqueConstraint("class_id", "session_date", name="uq_class_session_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    class_id: Mapped[int] = mapped_column(
        ForeignKey("training_classes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    room: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lesson_id: Mapped[int | None] = mapped_column(
        ForeignKey("course_lessons.id", ondelete="SET NULL"), nullable=True
    )
    is_cancelled: Mapped[bool] = mapped_column(
        Boolean,
        default=False, server_default="0", nullable=False
    )
    materials_open_early: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    cancellation_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    conflict_override: Mapped[bool] = mapped_column(
        Boolean,
        default=False, server_default="0", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )

    training_class: Mapped["TrainingClass"] = relationship(back_populates="sessions")
    teacher: Mapped["User | None"] = relationship(foreign_keys=[teacher_id])
    lesson: Mapped["CourseLesson | None"] = relationship()
    records: Mapped[list["AttendanceRecord"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class AttendanceRiskState(Base):
    __tablename__ = "attendance_risk_states"
    __table_args__ = (
        UniqueConstraint("class_id", "student_id", name="uq_attendance_risk_class_student"),
        CheckConstraint("absence_threshold_percent BETWEEN 0 AND 100", name="ck_attendance_risk_threshold"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("training_classes.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    is_at_risk: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    absent_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    completed_session_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    absence_threshold_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    activation_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    flagged_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.current_timestamp(), nullable=False)
class AttendanceRecord(Base):
    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint("session_id", "student_id", name="uq_session_student"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("attendance_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    marked_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    marked_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )

    session: Mapped[AttendanceSession] = relationship(back_populates="records")
    student: Mapped["User"] = relationship(foreign_keys=[student_id])
    marker: Mapped["User | None"] = relationship(foreign_keys=[marked_by])


class AttendanceScheduleHoliday(Base):
    __tablename__ = "attendance_schedule_holidays"

    id: Mapped[int] = mapped_column(primary_key=True)
    holiday_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class AttendanceChangeLog(Base):
    __tablename__ = "attendance_change_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(
        ForeignKey("attendance_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    changed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    old_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    new_status: Mapped[str] = mapped_column(String(24), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )


class AbsenceRequest(Base):
    __tablename__ = "absence_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("attendance_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    evidence_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    evidence_data: Mapped[bytes | None] = mapped_column(
        LargeBinary().with_variant(LONGBLOB(), "mysql"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False
    )
    decision_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decision_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
