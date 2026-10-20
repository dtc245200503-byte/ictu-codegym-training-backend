from sqlalchemy import Column, Integer, String, Date
from app.database import Base
from app.models.consultation import PRECISE_DATETIME


class ClassEnrollmentRecord(Base):
    """Immutable snapshots, retained independently of enrollment/account lifecycle."""
    __tablename__ = "class_enrollment_records"
    record_id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String(36), nullable=False, index=True)
    enrollment_id = Column(Integer, nullable=False, unique=True)
    student_id = Column(Integer, nullable=False, index=True)
    student_name = Column(String(100), nullable=False)
    student_code = Column(String(50), nullable=False)
    class_id = Column(Integer, nullable=False, index=True)
    class_name = Column(String(100), nullable=False)
    enrolled_on = Column(Date, nullable=False)
    actor_id = Column(Integer, nullable=False)
    actor_name = Column(String(100), nullable=False)
    created_at = Column(PRECISE_DATETIME, nullable=False)
