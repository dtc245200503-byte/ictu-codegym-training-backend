from sqlalchemy import Boolean, Column, Date, ForeignKey, Integer, String, Time, Text, UniqueConstraint
from app.database import Base
from app.models.consultation import PRECISE_DATETIME


class StudentClass(Base):
    """Map the existing enrollment table without changing its columns."""
    __tablename__ = "student_classes"
    student_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True)
    class_id = Column(Integer, ForeignKey("classes.class_id", ondelete="CASCADE"), primary_key=True)
    enrolled_date = Column(Date, nullable=True)


class ClassSession(Base):
    __tablename__ = "class_sessions"
    session_id = Column(Integer, primary_key=True, autoincrement=True)
    class_subject_id = Column(Integer, ForeignKey("class_subjects.id"), nullable=False, index=True)
    session_date = Column(Date, nullable=False, index=True)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=True)
    room_id = Column(Integer, ForeignKey("training_rooms.room_id"), nullable=True, index=True)
    room_name = Column(String(100), nullable=True)
    substitute_instructor_id = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    status = Column(String(20), nullable=False, default="scheduled", server_default="scheduled")
    cancellation_reason = Column(String(1000), nullable=True)
    schedule_version = Column(Integer, nullable=False, default=0, server_default="0")
    lesson_id = Column(Integer, ForeignKey("subject_lessons.id"), nullable=True, index=True)
    lesson_sequence = Column(Integer, nullable=True)
    lesson_objectives = Column(Text, nullable=True)
    topic = Column(String(255), nullable=False)
    absence_prefilled = Column(Boolean, nullable=False, default=False, server_default="0")
    attendance_version = Column(Integer, nullable=False, default=0)
    saved_at = Column(PRECISE_DATETIME, nullable=True)
    saved_by_name = Column(String(100), nullable=True)
    __table_args__ = (UniqueConstraint("class_subject_id", "session_date", "start_time", name="uq_class_session_slot"),)


class SessionAttendance(Base):
    __tablename__ = "session_attendance"
    session_id = Column(Integer, ForeignKey("class_sessions.session_id"), primary_key=True)
    # IDs and names are historical snapshots: deleting an account must not erase attendance.
    student_id = Column(Integer, primary_key=True)
    student_name = Column(String(100), nullable=False)
    student_code = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False)


class AttendanceHistory(Base):
    __tablename__ = "attendance_history"
    history_id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("class_sessions.session_id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    student_id = Column(Integer, nullable=False)
    student_name = Column(String(100), nullable=False)
    from_status = Column(String(20), nullable=True)
    to_status = Column(String(20), nullable=False)
    actor_id = Column(Integer, nullable=False)
    actor_name = Column(String(100), nullable=False)
    created_at = Column(PRECISE_DATETIME, nullable=False)
