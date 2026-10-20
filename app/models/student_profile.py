from sqlalchemy import Column, Integer, ForeignKey, String, Text
from app.database import Base
from app.models.consultation import PRECISE_DATETIME


class StudentProfileDetails(Base):
    __tablename__ = 'student_profile_details'
    student_id = Column(Integer, ForeignKey('users.user_id', ondelete='CASCADE'), primary_key=True)
    entry_level = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)
    updated_by_id = Column(Integer, nullable=True)
    updated_by_name = Column(String(100), nullable=True)
    updated_at = Column(PRECISE_DATETIME, nullable=True)
