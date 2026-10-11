from typing import Literal

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class LeadCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=150)
    email: EmailStr
    phone: str = Field(min_length=6, max_length=30)
    interested_program: str | None = Field(default=None, max_length=150)
    message: str | None = Field(default=None, max_length=3000)

    @field_validator("full_name", "phone", "interested_program", "message")
    @classmethod
    def strip_lead_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("full_name", "phone")
    @classmethod
    def required_text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Thông tin bắt buộc không được để trống.")
        return value.strip()


class LeadUpdate(BaseModel):
    status: Literal[
        "new", "contacted", "consulting", "trial_scheduled", "closed",
        "rejected", "enrolled", "qualified",
    ] | None = None
    notes: str | None = Field(default=None, max_length=3000)
    assigned_to_user_id: int | None = None
    rejection_reason: str | None = Field(default=None, max_length=1000)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_legacy_status(cls, value: str | None) -> str | None:
        return "closed" if value == "qualified" else value

    @field_validator("notes")
    @classmethod
    def strip_notes(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_update(self) -> "LeadUpdate":
        if not self.model_fields_set:
            raise ValueError("Cần cập nhật ít nhất một thông tin.")
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("Trạng thái không được để trống.")
        if (
            self.status == "rejected"
            and not (self.rejection_reason or "").strip()
        ):
            raise ValueError("Cần nhập lý do khi từ chối Lead.")
        return self

    @field_validator("rejection_reason")
    @classmethod
    def strip_rejection_reason(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class LeadActivityCreate(BaseModel):
    message: str = Field(min_length=1, max_length=3000)
    activity_type: Literal["call", "message", "meeting", "other"] = "call"
    scheduled_for: datetime | None = None

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Nội dung nhật ký không được để trống.")
        return value.strip()
