from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint, LargeBinary
from sqlalchemy.dialects.mysql import MEDIUMBLOB
from app.database import Base
from app.models.consultation import PRECISE_DATETIME


class AbsenceRequest(Base):
    __tablename__ = 'absence_requests'
    request_id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False, index=True)
    student_name = Column(String(100), nullable=False)
    student_code = Column(String(50), nullable=False)
    session_id = Column(Integer, ForeignKey('class_sessions.session_id'), nullable=False, index=True)
    reason = Column(String(2000), nullable=False)
    status = Column(String(20), nullable=False, default='pending', index=True)
    created_at = Column(PRECISE_DATETIME, nullable=False)
    reviewer_id = Column(Integer, nullable=True)
    reviewer_name = Column(String(100), nullable=True)
    review_reason = Column(String(1000), nullable=True)
    reviewed_at = Column(PRECISE_DATETIME, nullable=True)
    notification_read_at = Column(PRECISE_DATETIME, nullable=True)
    attachment_name = Column(String(255), nullable=True)
    attachment_type = Column(String(100), nullable=True)
    attachment_size = Column(Integer, nullable=True)
    __table_args__ = (UniqueConstraint('student_id', 'session_id', name='uq_absence_student_session'),)


class AbsenceEvidence(Base):
    __tablename__ = 'absence_evidence'
    request_id = Column(Integer, ForeignKey('absence_requests.request_id', ondelete='CASCADE'), primary_key=True)
    content = Column(LargeBinary().with_variant(MEDIUMBLOB(), 'mysql'), nullable=False)
