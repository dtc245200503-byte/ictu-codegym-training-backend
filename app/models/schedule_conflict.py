from sqlalchemy import Column, Integer, String, JSON
from app.database import Base
from app.models.consultation import PRECISE_DATETIME


class ScheduleWriteLock(Base):
    __tablename__ = "schedule_write_lock"
    id = Column(Integer, primary_key=True)
    revision = Column(Integer, nullable=False, default=0)


class ScheduleConflictOverride(Base):
    __tablename__ = "schedule_conflict_overrides"
    override_id = Column(Integer, primary_key=True, autoincrement=True)
    action = Column(String(100), nullable=False)
    actor_id = Column(Integer, nullable=False)
    actor_name = Column(String(100), nullable=False)
    conflicts = Column(JSON, nullable=False)
    created_at = Column(PRECISE_DATETIME, nullable=False)
