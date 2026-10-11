from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.core.security import DEFAULT_ACCOUNT_PASSWORD


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        if not any(char.isalpha() for char in value):
            raise ValueError("Mật khẩu phải có ít nhất một chữ cái.")
        if not any(char.isdigit() for char in value):
            raise ValueError("Mật khẩu phải có ít nhất một chữ số.")
        if not any(not char.isalnum() for char in value):
            raise ValueError("Mật khẩu phải có ít nhất một ký tự đặc biệt.")
        return value


class AccountCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=150)
    role: str | None = Field(
        default=None,
        pattern="^(admin|manager|admissions|accountant|teacher|ta|student|guest)$",
    )
    roles: list[str] | None = Field(default=None, min_length=1, max_length=8)
    password: str = Field(
        default=DEFAULT_ACCOUNT_PASSWORD, min_length=1, max_length=128
    )
    student_code: str | None = Field(default=None, max_length=50)
    phone: str | None = Field(default=None, max_length=30)

    @model_validator(mode="after")
    def require_roles(self) -> "AccountCreate":
        allowed = {"admin", "manager", "admissions", "accountant", "teacher", "ta", "student", "guest"}
        selected = self.roles if self.roles is not None else [self.role]
        if not selected or any(code not in allowed for code in selected):
            raise ValueError("Cần chọn vai trò hợp lệ.")
        if len(set(selected)) != len(selected):
            raise ValueError("Danh sách vai trò không được trùng lặp.")
        return self

    def selected_roles(self) -> list[str]:
        return self.roles if self.roles is not None else [self.role or ""]


class AccountUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    phone: str | None = Field(default=None, max_length=30)
    birth_date: date | None = None
    address: str | None = Field(default=None, max_length=255)


class SelfProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    phone: str | None = Field(default=None, max_length=30)
    birth_date: date | None = None
    address: str | None = Field(default=None, max_length=255)

    @field_validator("full_name", "phone", "address")
    @classmethod
    def strip_profile_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("full_name")
    @classmethod
    def require_full_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Họ tên không được để trống.")
        return value.strip() if value is not None else None


class AvatarUpload(BaseModel):
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    data_base64: str = Field(min_length=1, max_length=2_800_000)


class AccountStatusRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class RoleAssignmentRequest(BaseModel):
    role: str | None = Field(
        default=None,
        pattern="^(admin|manager|admissions|accountant|teacher|ta|student|guest)$",
    )
    roles: list[str] | None = Field(default=None, min_length=1, max_length=8)

    @model_validator(mode="after")
    def require_roles(self) -> "RoleAssignmentRequest":
        if self.roles is None and self.role is None:
            raise ValueError("Cần chọn ít nhất một vai trò.")
        if self.roles is not None and len(set(self.roles)) != len(self.roles):
            raise ValueError("Danh sách vai trò không được trùng lặp.")
        allowed = {"admin", "manager", "admissions", "accountant", "teacher", "ta", "student", "guest"}
        selected = self.roles if self.roles is not None else [self.role]
        if any(code not in allowed for code in selected):
            raise ValueError("Vai trò không hợp lệ.")
        return self

    def selected_roles(self) -> list[str]:
        return self.roles if self.roles is not None else [self.role]


class CourseLessonCreate(BaseModel):
    sequence: int = Field(ge=1, le=500)
    title: str = Field(min_length=2, max_length=150)
    objectives: str = Field(min_length=1, max_length=3000)
    description: str | None = Field(default=None, max_length=3000)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Chủ đề buổi học không được để trống.")
        return value.strip()


class CourseLessonUpdate(BaseModel):
    sequence: int | None = Field(default=None, ge=1, le=500)
    title: str | None = Field(default=None, min_length=2, max_length=150)
    objectives: str | None = Field(default=None, min_length=1, max_length=3000)
    description: str | None = Field(default=None, max_length=3000)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Chủ đề buổi học không được để trống.")
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_patch(self) -> "CourseLessonUpdate":
        if not self.model_fields_set:
            raise ValueError("Cần cung cấp ít nhất một thông tin.")
        if "sequence" in self.model_fields_set and self.sequence is None:
            raise ValueError("Thứ tự buổi học không được để trống.")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("Chủ đề buổi học không được để trống.")
        return self


class CourseLessonsClone(BaseModel):
    source_course_id: int


class CourseCreate(BaseModel):
    code: str = Field(min_length=2, max_length=30)
    name: str = Field(min_length=2, max_length=150)
    department: str = Field(min_length=2, max_length=100)
    credits: int = Field(ge=1, le=30)
    session_count: int = Field(default=16, ge=1, le=500)
    description: str | None = None
    prerequisite_id: int | None = None


class CourseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    department: str | None = Field(default=None, min_length=2, max_length=100)
    credits: int | None = Field(default=None, ge=1, le=30)
    session_count: int | None = Field(default=None, ge=1, le=500)
    description: str | None = None
    prerequisite_id: int | None = None
    is_active: bool | None = None


class StudyProgramCourse(BaseModel):
    course_id: int
    prerequisite_course_id: int | None = None


class StudyProgramCoursesUpdate(BaseModel):
    courses: list[StudyProgramCourse]


class StudyProgramSubjectCreate(BaseModel):
    subject_id: int


class StudyProgramSubject(BaseModel):
    subject_id: int
    prerequisite_subject_id: int | None = None


class StudyProgramSubjectsUpdate(BaseModel):
    subjects: list[StudyProgramSubject]


class TrainingClassCreate(BaseModel):
    code: str = Field(min_length=2, max_length=30)
    name: str = Field(min_length=2, max_length=150)
    course_id: int
    program_id: int | None = None
    teacher_id: int
    assistant_id: int | None = None
    assistant_ids: list[int] = Field(default_factory=list, max_length=100)
    room: str = Field(min_length=1, max_length=80)
    term: str = Field(min_length=2, max_length=50)
    day_of_week: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    capacity: int = Field(default=40, ge=1, le=500)

    @field_validator("end_time")
    @classmethod
    def validate_time_range(cls, value: time, info) -> time:
        start_time = info.data.get("start_time")
        if start_time is not None and value <= start_time:
            raise ValueError("Giờ kết thúc phải sau giờ bắt đầu.")
        return value


class TrainingClassUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    course_id: int | None = None
    teacher_id: int | None = None
    assistant_id: int | None = None
    assistant_ids: list[int] | None = Field(default=None, max_length=100)
    room: str | None = Field(default=None, min_length=1, max_length=80)
    term: str | None = Field(default=None, min_length=2, max_length=50)
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    start_time: time | None = None
    end_time: time | None = None
    capacity: int | None = Field(default=None, ge=1, le=500)
    status: Literal["preparing", "active", "completed", "cancelled"] | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> "TrainingClassUpdate":
        if not self.model_fields_set:
            raise ValueError("Cần cung cấp ít nhất một thông tin.")
        return self


class EnrollmentRequest(BaseModel):
    student_ids: list[int] = Field(min_length=1, max_length=500)


class StudentProfileUpdate(BaseModel):
    full_name: str = Field(min_length=1, max_length=150)
    phone: str | None = Field(default=None, max_length=30)
    birth_date: date | None = None
    address: str | None = Field(default=None, max_length=255)
    study_program: str | None = Field(default=None, max_length=150)


class ClassroomCreate(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    capacity: int = Field(ge=1, le=1000)
    online_url: str | None = Field(default=None, max_length=500)


class ClassroomUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=80)
    capacity: int | None = Field(default=None, ge=1, le=1000)
    online_url: str | None = Field(default=None, max_length=500)


class EnrollmentStatusUpdate(BaseModel):
    tuition_status: Literal["paid", "unpaid"] | None = None
    attendance_status: Literal[
        "not_recorded", "present", "absent", "late"
    ] | None = None

    @model_validator(mode="after")
    def require_status_update(self) -> "EnrollmentStatusUpdate":
        if self.tuition_status is None and self.attendance_status is None:
            raise ValueError("Cần cập nhật ít nhất một trạng thái.")
        return self


class AssignmentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=5000)
    due_at: datetime | None = None
