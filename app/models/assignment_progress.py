"""Current student outcome, written by the future submission/review workflows.

No row means not submitted. Reading/opening an assignment never creates a row.
"""
from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, String
from app.database import Base
from app.models.consultation import PRECISE_DATETIME


class AssignmentProgress(Base):
    __tablename__ = "assignment_progress"
    recipient_id = Column(Integer, ForeignKey("assignment_recipients.recipient_id"), primary_key=True)
    status = Column(String(24), nullable=False)
    submitted_at = Column(PRECISE_DATETIME, nullable=False)
    reviewed_at = Column(PRECISE_DATETIME, nullable=True)
    updated_at = Column(PRECISE_DATETIME, nullable=False)
    __table_args__ = (
        CheckConstraint("status IN ('submitted', 'graded', 'needs_revision')", name="ck_assignment_progress_status"),
        CheckConstraint("(status = 'submitted' AND reviewed_at IS NULL) OR (status IN ('graded', 'needs_revision') AND reviewed_at IS NOT NULL)", name="ck_assignment_progress_review"),
    )
