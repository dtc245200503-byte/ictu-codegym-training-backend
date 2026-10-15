from datetime import datetime, timezone
from typing import Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


def as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class CallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    next_callback_at: AwareDatetime | None


class CareLogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    interaction_type: Literal["call", "message", "meeting"]
    content: str = Field(min_length=1, max_length=5000)
    occurred_at: AwareDatetime
    callback_action: Literal["keep", "schedule", "complete"] = "keep"
    next_callback_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def consistent_callback(self):
        if (self.callback_action == "schedule") != (self.next_callback_at is not None):
            raise ValueError("Ngày nhắc gọi lại chỉ được gửi khi đặt lịch.")
        return self


class CareLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    log_id: int
    lead_id: int
    interaction_type: str
    content: str
    occurred_at: datetime
    created_at: datetime
    actor_id: int
    actor_name: str
    callback_action: str
    next_callback_at: datetime | None

    _utc_dates = field_validator("occurred_at", "created_at", "next_callback_at", mode="before")(as_utc)


class CareLogList(BaseModel):
    items: list[CareLogResponse]
    total: int
    page: int
    page_size: int
    next_callback_at: datetime | None
    callback_overdue: bool

    _utc_dates = field_validator("next_callback_at", mode="before")(as_utc)
