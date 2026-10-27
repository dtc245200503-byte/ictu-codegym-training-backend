from datetime import date, time
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ScheduleGenerateRequest(BaseModel):
    weekdays: list[int] = Field(min_length=1, max_length=7)
    start_date: date
    session_count: int = Field(ge=1, le=120)
    start_time: time | None = None
    end_time: time | None = None
    teacher_id: int | None = None
    room: str | None = Field(default=None, min_length=1, max_length=80)
    allow_conflicts: bool = False

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int]) -> list[int]:
        if any(day < 0 or day > 6 for day in value):
            raise ValueError("Ngày trong tuần phải nằm trong khoảng 0–6.")
        if len(set(value)) != len(value):
            raise ValueError("Danh sách thứ không được trùng.")
        return sorted(value)

    @model_validator(mode="after")
    def validate_times(self) -> "ScheduleGenerateRequest":
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.end_time <= self.start_time
        ):
            raise ValueError("Giờ kết thúc phải sau giờ bắt đầu.")
        return self


class ScheduleSessionUpdate(BaseModel):
    session_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    teacher_id: int | None = None
    room: str | None = Field(default=None, min_length=1, max_length=80)
    is_cancelled: bool | None = None
    materials_open_early: bool | None = None
    cancellation_reason: str | None = Field(default=None, max_length=1000)
    allow_conflicts: bool = False

    @field_validator("cancellation_reason")
    @classmethod
    def strip_reason(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ScheduleHolidayCreate(BaseModel):
    holiday_date: date
    name: str = Field(min_length=2, max_length=150)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()


class AttendanceMark(BaseModel):
    student_id: int
    status: Literal["present", "late", "excused_absent", "unexcused_absent"] | None


class AttendanceBulkUpdate(BaseModel):
    records: list[AttendanceMark] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_students(self) -> "AttendanceBulkUpdate":
        ids = [record.student_id for record in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("Mỗi học viên chỉ được xuất hiện một lần.")
        return self


class AbsenceRequestCreate(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)
    evidence_filename: str | None = Field(default=None, max_length=255)
    evidence_content_type: str | None = Field(default=None, max_length=100)
    evidence_base64: str | None = Field(default=None, max_length=14_000_000)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Lý do xin vắng không được để trống.")
        return value.strip()


class AbsenceRequestDecision(BaseModel):
    status: Literal["approved", "rejected"]
    decision_reason: str = Field(min_length=3, max_length=1000)

    @field_validator("decision_reason")
    @classmethod
    def strip_decision_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Cần nhập lý do duyệt hoặc từ chối.")
        return value.strip()


class EnrollmentChangeCreate(BaseModel):
    student_id: int
    class_id: int
    action: Literal["suspend", "transfer", "withdraw"]
    effective_date: date
    reason: str = Field(min_length=3, max_length=2000)
    target_class_id: int | None = None

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Lý do không được để trống.")
        return value.strip()

    @model_validator(mode="after")
    def validate_target_class(self) -> "EnrollmentChangeCreate":
        if self.action == "transfer" and self.target_class_id is None:
            raise ValueError("Cần chọn lớp đích khi chuyển lớp.")
        if self.action != "transfer" and self.target_class_id is not None:
            raise ValueError("Chỉ chuyển lớp mới nhận lớp đích.")
        return self
