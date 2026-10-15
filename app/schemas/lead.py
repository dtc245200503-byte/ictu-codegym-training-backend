from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from app.schemas.profile import UpdateProfileRequest

LeadStatus = Literal["new", "contacted", "qualified", "converted", "closed"]


class LeadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    full_name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=1, max_length=20)
    email: EmailStr | None = Field(default=None, max_length=255)
    source: str = Field(min_length=1, max_length=100)
    interest: str | None = Field(default=None, max_length=255)
    confirm_duplicate: bool = Field(default=False, strict=True)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value):
        return UpdateProfileRequest.validate_phone(value)

    @field_validator("email", "interest", mode="before")
    @classmethod
    def blank_to_none(cls, value):
        return None if isinstance(value, str) and not value.strip() else value


class DuplicateLead(BaseModel):
    lead_id: int
    full_name: str
    phone: str
    source: str
    interest: str | None


class DuplicateCheck(BaseModel):
    duplicates: list[DuplicateLead]
    total: int
    hidden_match: bool = False


class LeadResponse(BaseModel):
    lead_id: int
    full_name: str
    phone: str
    email: str | None
    source: str
    interest: str | None
    message: str | None
    status: str
    created_at: datetime
    duplicate_count: int
    assignee_id: int | None
    assignee_name: str | None
    next_callback_at: datetime | None = None
    callback_overdue: bool = False


class LeadListResponse(BaseModel):
    items: list[LeadResponse]
    total: int
    page: int
    page_size: int


class LeadAssigneeOption(BaseModel):
    user_id: int
    full_name: str


class LeadFilterOptions(BaseModel):
    sources: list[str]
    assignees: list[LeadAssigneeOption]
    can_view_all: bool


class AssignLeadsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    lead_ids: list[int] = Field(min_length=1, max_length=100)
    assignee_id: int = Field(gt=0, strict=True)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("lead_ids", mode="before")
    @classmethod
    def unique_positive_ids(cls, value):
        if not isinstance(value, list) or any(type(item) is not int or item <= 0 for item in value):
            raise ValueError("Mã lead phải là số nguyên dương.")
        if len(set(value)) != len(value):
            raise ValueError("Danh sách lead không được trùng lặp.")
        return value


class AssignmentResponse(BaseModel):
    changed: int
    unchanged: int
    message: str


class AdvisorResponse(BaseModel):
    user_id: int
    full_name: str
    email: str


class AssignmentHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    history_id: int
    lead_id: int
    from_assignee_id: int | None
    to_assignee_id: int | None
    actor_id: int
    from_assignee_name: str | None
    to_assignee_name: str | None
    actor_name: str
    note: str | None
    created_at: datetime


class AssignmentHistoryList(BaseModel):
    items: list[AssignmentHistoryResponse]
    total: int
    page: int
    page_size: int
