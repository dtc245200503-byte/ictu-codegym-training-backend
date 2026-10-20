from sqlalchemy import Column, ForeignKey, Integer, String, Text
from app.database import Base
from app.models.consultation import PRECISE_DATETIME


class LeadConversion(Base):
    """Immutable admission snapshot plus recoverable welcome-email delivery state."""
    __tablename__ = "lead_conversions"

    lead_id = Column(Integer, ForeignKey("consultation_leads.lead_id"), primary_key=True)
    student_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, unique=True)
    student_code = Column(String(50), nullable=False, unique=True)
    full_name = Column(String(100), nullable=False)
    phone = Column(String(20), nullable=False)
    email = Column(String(100), nullable=False)
    source = Column(String(100), nullable=False)
    interest = Column(String(255))
    message = Column(Text)
    actor_id = Column(Integer, nullable=False)
    actor_name = Column(String(100), nullable=False)
    created_at = Column(PRECISE_DATETIME, nullable=False)
    email_status = Column(String(20), nullable=False, default="pending")
    email_attempt_at = Column(PRECISE_DATETIME)
    email_sent_at = Column(PRECISE_DATETIME)
    email_attempt_key = Column(String(32))
    # No FK: reset-token retention must not delete the admission history.
    reset_id = Column(Integer)
