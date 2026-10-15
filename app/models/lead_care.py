from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text
from app.database import Base
from app.models.consultation import PRECISE_DATETIME


class LeadCareLog(Base):
    __tablename__ = "lead_care_logs"
    __table_args__ = (Index("ix_lead_care_timeline", "lead_id", "occurred_at", "log_id"),)

    log_id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Integer, ForeignKey("consultation_leads.lead_id"), nullable=False)
    interaction_type = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    occurred_at = Column(PRECISE_DATETIME, nullable=False)
    created_at = Column(PRECISE_DATETIME, nullable=False)
    actor_id = Column(Integer, nullable=False)
    actor_name = Column(String(100), nullable=False)
    callback_action = Column(String(20), nullable=False, default="keep")
    next_callback_at = Column(PRECISE_DATETIME, nullable=True)
