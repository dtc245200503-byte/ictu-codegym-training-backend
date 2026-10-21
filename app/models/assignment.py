from sqlalchemy import Column, ForeignKey, Integer, LargeBinary, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import MEDIUMBLOB
from sqlalchemy.orm import deferred
from app.database import Base
from app.models.consultation import PRECISE_DATETIME


class Assignment(Base):
    __tablename__ = "class_assignments"
    assignment_id = Column(Integer, primary_key=True, autoincrement=True)
    class_subject_id = Column(Integer, ForeignKey("class_subjects.id"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("class_sessions.session_id"), nullable=False, index=True)
    author_id = Column(Integer, nullable=False, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    due_at = Column(PRECISE_DATETIME, nullable=False)
    max_points = Column(Numeric(8, 2), nullable=False)
    status = Column(String(20), nullable=False, default="draft")
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(PRECISE_DATETIME, nullable=False)
    updated_at = Column(PRECISE_DATETIME, nullable=False)
    published_at = Column(PRECISE_DATETIME, nullable=True)


class AssignmentAttachment(Base):
    __tablename__ = "assignment_attachments"
    attachment_id = Column(Integer, primary_key=True, autoincrement=True)
    assignment_id = Column(Integer, ForeignKey("class_assignments.assignment_id"), nullable=False, index=True)
    filename = Column(String(200), nullable=False)
    size = Column(Integer, nullable=False)
    content = deferred(Column(LargeBinary().with_variant(MEDIUMBLOB(), "mysql"), nullable=False))


class AssignmentRecipient(Base):
    """Delivery snapshot, also the student's notification and read access."""
    __tablename__ = "assignment_recipients"
    recipient_id = Column(Integer, primary_key=True, autoincrement=True)
    assignment_id = Column(Integer, ForeignKey("class_assignments.assignment_id"), nullable=False, index=True)
    student_id = Column(Integer, nullable=False, index=True)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(PRECISE_DATETIME, nullable=False)
    read_at = Column(PRECISE_DATETIME, nullable=True)
    __table_args__ = (UniqueConstraint("assignment_id", "student_id", name="uq_assignment_recipient"),)
