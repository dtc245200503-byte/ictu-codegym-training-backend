from sqlalchemy import Column, Date, ForeignKey, Integer, String, UniqueConstraint, Index
from app.database import Base
from app.models.consultation import PRECISE_DATETIME


class StudentEnrollment(Base):
    """Effective intervals are [started_on, ended_on); null start supports legacy rows."""
    __tablename__ = "student_enrollments"
    enrollment_id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.class_id"), nullable=False)
    started_on = Column(Date, nullable=True)
    ended_on = Column(Date, nullable=True)
    end_status = Column(String(20), nullable=True)
    origin = Column(String(20), nullable=False, default="enrolled")
    __table_args__ = (Index("ix_student_enrollment_pair", "student_id", "class_id"),)


class StudentEnrollmentHistory(Base):
    __tablename__ = "student_enrollment_history"
    history_id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, nullable=False, index=True)
    student_name = Column(String(100), nullable=False)
    student_code = Column(String(50), nullable=False)
    enrollment_id = Column(Integer, nullable=False)
    operation = Column(String(20), nullable=False)
    effective_date = Column(Date, nullable=False)
    reason = Column(String(1000), nullable=False)
    from_class_id = Column(Integer, nullable=False)
    from_class_name = Column(String(100), nullable=False)
    to_class_id = Column(Integer, nullable=True)
    to_class_name = Column(String(100), nullable=True)
    actor_id = Column(Integer, nullable=False)
    actor_name = Column(String(100), nullable=False)
    created_at = Column(PRECISE_DATETIME, nullable=False)
    __table_args__ = (UniqueConstraint("enrollment_id", name="uq_student_enrollment_end"),)
