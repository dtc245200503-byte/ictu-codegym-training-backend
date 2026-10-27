from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal
import re
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.core.security import generate_temporary_password


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
        default_factory=generate_temporary_password, min_length=1, max_length=128
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
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    phone: str | None = Field(default=None, max_length=30)
    birth_date: date | None = None
    address: str | None = Field(default=None, max_length=255)

    @field_validator("full_name", "phone", "address")
    @classmethod
    def strip_profile_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("phone")
    @classmethod
    def validate_vietnamese_phone(cls, value: str | None) -> str | None:
        if value and not re.fullmatch(r"(?:0\d{9}|\+84\d{9})", value):
            raise ValueError("Sá»‘ Ä‘iá»‡n thoáº¡i pháº£i theo Ä‘á»‹nh dáº¡ng Viá»‡t Nam.")
        return value

    @field_validator("full_name")
    @classmethod
    def require_full_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Họ tên không được để trống.")
        return value.strip() if value is not None else None


class AvatarUpload(BaseModel):
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    data_base64: str = Field(min_length=1, max_length=2_800_000)
    crop_x: int | None = Field(default=None, ge=0)
    crop_y: int | None = Field(default=None, ge=0)
    crop_size: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_crop(self) -> "AvatarUpload":
        supplied = (self.crop_x is not None, self.crop_y is not None, self.crop_size is not None)
        if any(supplied) and not all(supplied):
            raise ValueError("crop_x, crop_y, and crop_size must be supplied together.")
        return self


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
    learning_outcomes: str | None = Field(default=None, max_length=10000)
    prerequisite_id: int | None = None


class CourseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    department: str | None = Field(default=None, min_length=2, max_length=100)
    credits: int | None = Field(default=None, ge=1, le=30)
    session_count: int | None = Field(default=None, ge=1, le=500)
    description: str | None = None
    learning_outcomes: str | None = Field(default=None, max_length=10000)
    prerequisite_id: int | None = None
    is_active: bool | None = None


class StudyProgramCourse(BaseModel):
    course_id: int
    prerequisite_course_id: int | None = None
    weight: Decimal = Field(default=Decimal("0"), ge=0, max_digits=8, decimal_places=2)


class StudyProgramCoursesUpdate(BaseModel):
    courses: list[StudyProgramCourse]


class StudyProgramCreate(BaseModel):
    code: str = Field(min_length=2, max_length=30, pattern="^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=150)
    degree_level: str = Field(default="Đại học", min_length=2, max_length=30)
    total_credits: int = Field(ge=1, le=1000)
    duration_semesters: int = Field(default=8, ge=1, le=40)
    description: str | None = Field(default=None, max_length=10000)
    standard_tuition_vnd: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=0)


class StudyProgramUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    degree_level: str | None = Field(default=None, min_length=2, max_length=30)
    total_credits: int | None = Field(default=None, ge=1, le=1000)
    duration_semesters: int | None = Field(default=None, ge=1, le=40)
    description: str | None = Field(default=None, max_length=10000)
    is_active: bool | None = None
    standard_tuition_vnd: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=0)

    @model_validator(mode="after")
    def require_update(self) -> "StudyProgramUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be supplied.")
        if "standard_tuition_vnd" in self.model_fields_set and self.standard_tuition_vnd is None:
            raise ValueError("Standard tuition cannot be null.")
        return self


class StudyProgramSubjectCreate(BaseModel):
    subject_id: int
    weight: Decimal = Field(default=Decimal("0"), ge=0, max_digits=8, decimal_places=2)


class StudyProgramSubject(BaseModel):
    subject_id: int
    prerequisite_subject_id: int | None = None
    weight: Decimal = Field(default=Decimal("0"), ge=0, max_digits=8, decimal_places=2)


class StudyProgramSubjectsUpdate(BaseModel):
    subjects: list[StudyProgramSubject]


class ProgramFeeScheduleCreate(BaseModel):
    amount_vnd: Decimal = Field(ge=0, max_digits=18, decimal_places=0)
    valid_from: date
    valid_to: date | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> "ProgramFeeScheduleCreate":
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("Ngày kết thúc hiệu lực phải sau hoặc bằng ngày bắt đầu.")
        return self


class TuitionInstallmentInput(BaseModel):
    amount_vnd: Decimal = Field(gt=0, max_digits=18, decimal_places=0)
    due_date: date


class TuitionPlanBatchCreate(BaseModel):
    class_id: int = Field(ge=1)
    fee_schedule_id: int = Field(ge=1)
    applied_on: date
    installments: list[TuitionInstallmentInput] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def validate_installments(self) -> "TuitionPlanBatchCreate":
        if sum((item.amount_vnd for item in self.installments), Decimal("0")) <= 0:
            raise ValueError("Tổng kế hoạch phải lớn hơn 0 VND.")
        if [item.due_date for item in self.installments] != sorted(
            item.due_date for item in self.installments
        ):
            raise ValueError("Ngày đến hạn các đợt phải theo thứ tự tăng dần.")
        return self


class TuitionPaymentCreate(BaseModel):
    amount_vnd: Decimal = Field(gt=0, max_digits=18, decimal_places=0)
    method: Literal["cash", "bank_transfer"]
    paid_on: date
    idempotency_key: str = Field(min_length=8, max_length=100)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("idempotency_key")
    @classmethod
    def strip_idempotency_key(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Idempotency key không được để trống.")
        return value

    @field_validator("note")
    @classmethod
    def strip_payment_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class TuitionPaymentReversalCreate(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=100)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("idempotency_key", "reason")
    @classmethod
    def strip_reversal_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Giá trị không được để trống.")
        return value


class TuitionDiscountCreate(BaseModel):
    kind: Literal["fixed", "percent"]
    value: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def strip_discount_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Discount reason cannot be blank.")
        return value

    @model_validator(mode="after")
    def validate_discount_value(self):
        if self.kind == "percent" and self.value > Decimal("100"):
            raise ValueError("Discount percentage cannot exceed 100%.")
        if self.kind == "fixed" and self.value != self.value.to_integral_value():
            raise ValueError("Fixed discount must be an integer VND amount.")
        return self


class TrainingClassCreate(BaseModel):
    code: str = Field(min_length=2, max_length=30)
    name: str = Field(min_length=2, max_length=150)
    course_id: int
    program_id: int | None = None
    planned_start_date: date | None = None
    delivery_mode: Literal["onsite", "online", "hybrid"] = "onsite"
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
    program_id: int | None = Field(default=None, ge=1)
    planned_start_date: date | None = None
    delivery_mode: Literal["onsite", "online", "hybrid"] | None = None
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

    @model_validator(mode="after")
    def require_unique_students(self) -> "EnrollmentRequest":
        if any(student_id < 1 for student_id in self.student_ids):
            raise ValueError("Student identifiers must be positive.")
        if len(set(self.student_ids)) != len(self.student_ids):
            raise ValueError("A student can only appear once in a bulk enrollment request.")
        return self


class StudentProfileUpdate(BaseModel):
    full_name: str = Field(min_length=1, max_length=150)
    phone: str | None = Field(default=None, max_length=30)
    birth_date: date | None = None
    address: str | None = Field(default=None, max_length=255)
    study_program: str | None = Field(default=None, max_length=150)
    entry_level: str | None = Field(default=None, max_length=150)
    notes: str | None = Field(default=None, max_length=3000)

    @field_validator("full_name", "phone", "address", "study_program", "entry_level", "notes")
    @classmethod
    def normalize_student_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ClassroomCreate(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    capacity: int = Field(ge=1, le=1000)
    online_url: str | None = Field(default=None, max_length=500)

    @field_validator("online_url")
    @classmethod
    def require_secure_online_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        parsed = urlsplit(normalized)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Online classroom links must use HTTPS and include a host.")
        return normalized


class ClassroomUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=80)
    capacity: int | None = Field(default=None, ge=1, le=1000)
    online_url: str | None = Field(default=None, max_length=500)

    @field_validator("online_url")
    @classmethod
    def require_secure_online_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        normalized = value.strip()
        parsed = urlsplit(normalized)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Online classroom links must use HTTPS and include a host.")
        return normalized


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
    max_score: Decimal | None = Field(
        default=None, gt=0, max_digits=18, decimal_places=2
    )
    status: Literal["draft", "published"] = "published"
    session_id: int | None = Field(default=None, ge=1)
    grade_component_key: str | None = Field(default=None, min_length=1, max_length=50, pattern="^[a-zA-Z][a-zA-Z0-9_-]*$")
    late_submission_policy: Literal["BLOCK_LATE", "ALLOW_LATE"] = "BLOCK_LATE"


class AssignmentReworkRequestCreate(BaseModel):
    feedback: str = Field(min_length=1, max_length=5000)
    due_at: datetime

    @field_validator("feedback")
    @classmethod
    def nonblank_feedback(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Rework feedback is required.")
        return value.strip()


class ClassReminderPreferenceUpdate(BaseModel):
    class_reminder_opt_out: bool


class SessionMaterialsVisibilityUpdate(BaseModel):
    materials_open_early: bool


class ProgramAttendancePolicyUpdate(BaseModel):
    absence_threshold_percent: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)


class AnnouncementCreate(BaseModel):
    class_ids: list[int] = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=150)
    body: str = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def normalize_and_validate(self):
        self.title = self.title.strip()
        self.body = self.body.strip()
        if not self.title or not self.body:
            raise ValueError("Announcement title and body are required.")
        if any(class_id < 1 for class_id in self.class_ids):
            raise ValueError("Class identifiers must be positive.")
        if len(set(self.class_ids)) != len(self.class_ids):
            raise ValueError("Class identifiers must be unique.")
        return self


class SessionLearningLinkCreate(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    url: AnyHttpUrl

    @model_validator(mode="after")
    def validate_external_link(self):
        self.title = self.title.strip()
        if not self.title:
            raise ValueError("Link title is required.")
        if self.url.scheme != "https" or self.url.username is not None or self.url.password is not None:
            raise ValueError("External learning links must use HTTPS and cannot contain credentials.")
        return self


class GradePolicyComponentInput(BaseModel):
    key: str = Field(min_length=1, max_length=50, pattern="^[a-zA-Z][a-zA-Z0-9_-]*$")
    name: str = Field(min_length=1, max_length=100)
    weight: Decimal = Field(gt=0, le=100, max_digits=5, decimal_places=2)
    required: bool = True


class GradePolicyCreate(BaseModel):
    program_id: int = Field(ge=1)
    course_id: int = Field(ge=1)
    class_id: int | None = Field(default=None, ge=1)
    components: list[GradePolicyComponentInput] = Field(min_length=1, max_length=20)
    pass_score: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)
    critical_component_key: str | None = Field(default=None, max_length=50)
    critical_score: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)

    @model_validator(mode="after")
    def validate_policy(self):
        keys = [component.key for component in self.components]
        if len(keys) != len(set(keys)):
            raise ValueError("Grade component keys must be unique.")
        if sum((component.weight for component in self.components), Decimal("0")) != Decimal("100"):
            raise ValueError("Grade component weights must sum to 100%.")
        if (self.critical_component_key is None) != (self.critical_score is None):
            raise ValueError("Critical component and threshold must be configured together.")
        if self.critical_component_key is not None and self.critical_component_key not in keys:
            raise ValueError("Critical component must exist in the configured components.")
        return self


class GradeReopenRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def trim_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A reopen reason is required.")
        return value.strip()


class AssignmentSubmissionCreate(BaseModel):
    repository_url: AnyHttpUrl

    @field_validator("repository_url")
    @classmethod
    def require_https_repository(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https" or value.username is not None or value.password is not None:
            raise ValueError("Liên kết bài nộp phải dùng HTTPS và không chứa thông tin đăng nhập.")
        return value


class AssignmentGradeCreate(BaseModel):
    submission_id: int = Field(ge=1)
    score: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    feedback: str | None = Field(default=None, max_length=5000)
    internal_comment: str | None = Field(default=None, max_length=5000)
    rubric_scores: list[dict] | None = Field(default=None, max_length=30)


class RubricCriterionInput(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=1000)
    weight: Decimal = Field(gt=0, le=100, max_digits=5, decimal_places=2)


class RubricTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    criteria: list[RubricCriterionInput] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def weights_must_total_100(self):
        if sum((item.weight for item in self.criteria), Decimal("0")) != Decimal("100"):
            raise ValueError("Tổng trọng số rubric phải bằng 100%.")
        return self


class RubricScoreInput(BaseModel):
    criterion_id: int = Field(ge=1)
    score_percent: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)
    comment: str | None = Field(default=None, max_length=2000)
