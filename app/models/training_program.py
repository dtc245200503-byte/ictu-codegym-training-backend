from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint

from app.database import Base


class TrainingProgram(Base):
    # Existing classes.course_id links courses to their reusable program.
    __tablename__ = "courses"

    course_id = Column(Integer, primary_key=True, autoincrement=True)
    course_name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    duration_months = Column(Integer, nullable=True, default=1)
    code = Column(String(50), nullable=False)
    total_duration_hours = Column(Numeric(8, 2), nullable=False, default=0)
    standard_tuition = Column(Numeric(12, 0), nullable=False, default=0)
    status = Column(String(20), nullable=False, default="active")
    deleted_at = Column(DateTime, nullable=True)
    __table_args__ = (UniqueConstraint("code", name="uq_courses_program_code"),)


class TrainingClass(Base):
    __tablename__ = "classes"

    class_id = Column(Integer, primary_key=True, autoincrement=True)
    class_name = Column(String(100), nullable=False)
    class_code = Column(String(50), nullable=True, unique=True)
    learning_mode = Column(String(20), nullable=True)
    curriculum_snapshot = Column(Boolean, nullable=False, default=False, server_default="0")
    course_id = Column(Integer, ForeignKey("courses.course_id"), nullable=True)
    instructor_id = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    start_date = Column(Date, nullable=True)
    max_students = Column(Integer, nullable=True)
    # Legacy classes have no completion data: conservatively treat them as running.
    status = Column(String(20), nullable=False, default="in_progress")
