from sqlalchemy import Boolean, Column, Integer, Numeric, String, Text, UniqueConstraint
from app.database import Base
from app.models.consultation import PRECISE_DATETIME


class ProgramAbsencePolicy(Base):
    __tablename__ = "program_absence_policies"
    course_id = Column(Integer, primary_key=True)
    threshold_percent = Column(Numeric(5, 2), nullable=False, default=20)


class AttendanceRiskSync(Base):
    __tablename__ = "attendance_risk_sync"
    id = Column(Integer, primary_key=True)
    revision = Column(Integer, nullable=False, default=0)


class AttendanceRisk(Base):
    __tablename__ = "attendance_risks"
    class_id = Column(Integer, primary_key=True)
    student_id = Column(Integer, primary_key=True)
    active = Column(Boolean, nullable=False, default=False)
    activation = Column(Integer, nullable=False, default=0)
    absent_sessions = Column(Integer, nullable=False)
    scheduled_sessions = Column(Integer, nullable=False)
    threshold_percent = Column(Numeric(5, 2), nullable=False)
    activated_at = Column(PRECISE_DATETIME, nullable=True)
    updated_at = Column(PRECISE_DATETIME, nullable=False)


class AttendanceRiskNotification(Base):
    __tablename__ = "attendance_risk_notifications"
    notification_id = Column(Integer, primary_key=True, autoincrement=True)
    class_id = Column(Integer, nullable=False)
    student_id = Column(Integer, nullable=False)
    activation = Column(Integer, nullable=False)
    recipient_id = Column(Integer, nullable=False, index=True)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(PRECISE_DATETIME, nullable=False)
    read_at = Column(PRECISE_DATETIME, nullable=True)
    __table_args__ = (UniqueConstraint("class_id", "student_id", "activation", "recipient_id", name="uq_attendance_risk_notice"),)
