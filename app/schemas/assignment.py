from datetime import datetime, timezone
from decimal import Decimal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class AssignmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    class_subject_id: int = Field(gt=0, strict=True)
    session_id: int = Field(gt=0, strict=True)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=20000)
    due_at: AwareDatetime
    max_points: Decimal = Field(gt=0, le=10000, max_digits=7, decimal_places=2)

    @field_validator("due_at")
    @classmethod
    def utc_deadline(cls, value: datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None)


class AssignmentUpdate(AssignmentInput):
    expected_version: int = Field(gt=0, strict=True)


class PublishAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(gt=0, strict=True)
