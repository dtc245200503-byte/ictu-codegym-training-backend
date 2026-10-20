from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class EnrollmentChange(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    class_id: int = Field(gt=0, strict=True)
    enrollment_id: int | None = Field(gt=0, strict=True)
    operation: Literal["deferred", "transferred", "withdrawn"]
    effective_date: date
    reason: str = Field(min_length=1, max_length=1000)
    to_class_id: int | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode="after")
    def transfer_target(self):
        if self.effective_date.year < 1000:
            raise ValueError("Ngày hiệu lực phải từ năm 1000 trở đi.")
        if self.operation == "transferred":
            if self.to_class_id is None or self.to_class_id == self.class_id:
                raise ValueError("Chuyển lớp cần chọn lớp đích khác lớp hiện tại.")
        elif self.to_class_id is not None:
            raise ValueError("Chỉ chuyển lớp mới có lớp đích.")
        return self
