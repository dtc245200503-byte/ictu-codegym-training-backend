from datetime import date, time
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.schedule_conflict import ConflictConfirmation

AttendanceStatus = Literal["present", "late", "excused_absence", "unexcused_absence"]
PositiveId = Annotated[int, Field(gt=0, strict=True)]


class AttendanceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    student_id: PositiveId
    status: AttendanceStatus


class SaveAttendance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0, strict=True)
    items: list[AttendanceItem] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def unique_students(self):
        if len({item.student_id for item in self.items}) != len(self.items):
            raise ValueError("Danh sách điểm danh không được trùng học viên.")
        return self


class CreateClassSession(ConflictConfirmation):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    class_subject_id: PositiveId
    session_date: date
    start_time: time
    end_time: time | None = None
    room_id: PositiveId | None = None
    room_name: str | None = Field(default=None, max_length=100)
    topic: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def local_time_only(self):
        if self.start_time.tzinfo is not None or self.start_time.second or self.start_time.microsecond:
            raise ValueError("Giờ học dùng giờ Việt Nam, chính xác đến phút.")
        if self.end_time is not None:
            if self.end_time.tzinfo is not None or self.end_time.second or self.end_time.microsecond or self.end_time <= self.start_time:
                raise ValueError("Giờ kết thúc phải sau giờ bắt đầu trong cùng ngày, chính xác đến phút.")
        self.room_name = " ".join(self.room_name.split()) if self.room_name else None
        self.room_name = self.room_name or None
        return self


class EnrollStudents(BaseModel):
    model_config = ConfigDict(extra="forbid")
    student_ids: list[PositiveId] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def unique_students(self):
        if len(set(self.student_ids)) != len(self.student_ids):
            raise ValueError("Danh sách không được trùng học viên.")
        return self
