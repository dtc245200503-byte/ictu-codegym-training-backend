from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from app.database import Base # Hoặc từ app.db.base tùy cấu hình dự án của bạn

class Subject(Base):
    __tablename__ = "subjects"

    id = Column(Integer, autoincrement=True, primary_key=True, index=True)
    subject_name = Column(String(255), nullable=False) # Hoặc Column(String(255), nullable=False) cho tên môn
    description = Column(Text, nullable=True)
    code = Column(String(50), nullable=False, unique=True)
    session_count = Column(Integer, nullable=False, default=1)
    weight = Column(Numeric(6, 2), nullable=False, default=1)
    deleted_at = Column(DateTime, nullable=True)


class ClassSubject(Base):
    """Keep subject usage even after the program's syllabus changes."""
    __tablename__ = "class_subjects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    class_id = Column(Integer, ForeignKey("classes.class_id"), nullable=False, index=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False, index=True)
    instructor_id = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    __table_args__ = (UniqueConstraint("class_id", "subject_id", name="uq_class_subject"),)


class ClassSubjectAssistant(Base):
    """Teaching assistants assigned to a class subject."""
    __tablename__ = "class_subject_assistants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    class_subject_id = Column(Integer, ForeignKey("class_subjects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    __table_args__ = (UniqueConstraint("class_subject_id", "user_id", name="uq_class_subject_assistant"),)
