from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.mysql import DATETIME
from app.database import Base

# Keep sub-second timing on MySQL; DATETIME(0) otherwise rounds issued times.
PRECISE_DATETIME = DateTime().with_variant(DATETIME(fsp=6), "mysql")


class ConsultationLead(Base):
    __tablename__ = "consultation_leads"

    lead_id = Column(Integer, primary_key=True, autoincrement=True)
    full_name = Column(String(100), nullable=False)
    phone = Column(String(20), nullable=False, index=True)
    email = Column(String(255), nullable=True)
    interest = Column(String(255), nullable=True)
    message = Column(Text, nullable=True)
    source = Column(String(100), nullable=False, default="Biểu mẫu công khai")
    deleted_at = Column(PRECISE_DATETIME, nullable=True)
    source = Column(String(100), nullable=False, default="Biểu mẫu công khai")
    deleted_at = Column(PRECISE_DATETIME, nullable=True)
    status = Column(String(20), nullable=False, default="new", index=True)
    created_at = Column(PRECISE_DATETIME, nullable=False)
    next_callback_at = Column(PRECISE_DATETIME, nullable=True, index=True)
    assignee_id = Column(Integer, ForeignKey("users.user_id"), nullable=True, index=True)


class ConsultationChallenge(Base):
    __tablename__ = "consultation_challenges"

    token_hash = Column(String(64), primary_key=True)
    client_key = Column(String(64), nullable=False)
    answer_hash = Column(String(64), nullable=False)
    issued_at = Column(PRECISE_DATETIME, nullable=False)
    expires_at = Column(PRECISE_DATETIME, nullable=False, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    lead_id = Column(Integer, ForeignKey("consultation_leads.lead_id"), nullable=True)


class ConsultationRateBucket(Base):
    __tablename__ = "consultation_rate_buckets"

    key = Column(String(64), primary_key=True)
    window_start = Column(PRECISE_DATETIME, nullable=False, index=True)
    count = Column(Integer, nullable=False, default=0)
