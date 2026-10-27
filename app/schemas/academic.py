from decimal import Decimal

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class GraduationThresholdUpdate(BaseModel):
    attendance_threshold_percent: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)


class GraduationConfirmBatch(BaseModel):
    student_ids: list[int] = Field(min_length=1, max_length=500)

    @field_validator("student_ids")
    @classmethod
    def unique_students(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("student_ids must be unique")
        return value


class SurveyQuestionInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
    question_type: Literal["rating", "text"]
    is_required: bool = True
    scale_min: int | None = Field(default=None, ge=0, le=10)
    scale_max: int | None = Field(default=None, ge=1, le=10)

    @model_validator(mode="after")
    def validate_scale(self):
        if self.question_type == "rating":
            if self.scale_min is None and self.scale_max is None:
                self.scale_min, self.scale_max = 1, 5
            if self.scale_min is None or self.scale_max is None or self.scale_min >= self.scale_max:
                raise ValueError("Rating questions require a valid scale range.")
        elif self.scale_min is not None or self.scale_max is not None:
            raise ValueError("Text questions cannot configure rating scale bounds.")
        return self


class ClassSurveyCreate(BaseModel):
    class_id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    deadline: datetime
    questions: list[SurveyQuestionInput] = Field(min_length=1, max_length=50)


class SurveyResponseCreate(BaseModel):
    answers: dict[int, int | str] = Field(max_length=50)
