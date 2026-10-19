from sqlalchemy import Boolean, Column, Integer, String
from app.database import Base


class Room(Base):
    __tablename__ = "training_rooms"
    room_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    name_key = Column(String(300), nullable=False, unique=True)
    kind = Column(String(20), nullable=False)
    capacity = Column(Integer, nullable=True)
    meeting_url = Column(String(2000), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=0)
