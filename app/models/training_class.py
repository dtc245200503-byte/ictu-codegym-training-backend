from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Index,
    JSON,
    Numeric,
    String,
    Table,
    Time,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class_assistants = Table(
    "class_assistants",
    Base.metadata,
    Column(
        "class_id",
        ForeignKey("training_classes.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "assistant_id",
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)
Index("ix_class_assistants_assistant_id", class_assistants.c.assistant_id)


class TrainingClass(Base):
    __tablename__ = "training_classes"
    __table_args__ = (
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_class_weekday"),
        CheckConstraint("start_time < end_time", name="ck_class_time_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="RESTRICT"), nullable=False
    )
    program_id: Mapped[int | None] = mapped_column(
        ForeignKey("study_programs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    planned_start_date: Mapped[date | None] = mapped_column(nullable=True)
    delivery_mode: Mapped[str] = mapped_column(String(20), default="onsite", server_default="onsite", nullable=False)
    program_curriculum_snapshot: Mapped[list | None] = mapped_column(JSON, nullable=True)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    assistant_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    room: Mapped[str] = mapped_column(String(80), nullable=False)
    term: Mapped[str] = mapped_column(String(50), nullable=False)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    capacity: Mapped[int] = mapped_column(
        Integer, default=40, server_default="40", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), default="preparing", server_default="preparing", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )

    course: Mapped["Course"] = relationship()
    teacher: Mapped["User"] = relationship(foreign_keys=[teacher_id])
    assistant: Mapped["User | None"] = relationship(foreign_keys=[assistant_id])
    assistants: Mapped[list["User"]] = relationship(
        secondary=class_assistants,
        primaryjoin="TrainingClass.id == class_assistants.c.class_id",
        secondaryjoin="User.id == class_assistants.c.assistant_id",
        foreign_keys=[class_assistants.c.class_id, class_assistants.c.assistant_id],
    )
    enrollments: Mapped[list["ClassEnrollment"]] = relationship(
        back_populates="training_class", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["AttendanceSession"]] = relationship(
        back_populates="training_class", cascade="all, delete-orphan"
    )


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        CheckConstraint(
            "late_submission_policy IN ('BLOCK_LATE', 'ALLOW_LATE')",
            name="ck_assignment_late_policy",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    class_id: Mapped[int] = mapped_column(
        ForeignKey("training_classes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    max_score: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="published", server_default="published", nullable=False
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("attendance_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    grade_component_key: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    late_submission_policy: Mapped[str] = mapped_column(
        String(16), default="BLOCK_LATE", server_default="BLOCK_LATE", nullable=False
    )
    roster_snapshotted_at: Mapped[datetime | None] = mapped_column(
        DateTime(), nullable=True
    )
    rubric_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )

    training_class: Mapped["TrainingClass"] = relationship()
    creator: Mapped["User | None"] = relationship()


class AssignmentRecipient(Base):
    __tablename__ = "assignment_recipients"
    __table_args__ = (
        UniqueConstraint("assignment_id", "student_id", name="uq_assignment_recipient"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )


class AssignmentSubmission(Base):
    """Append-only repository-link submissions; each row is one version."""

    __tablename__ = "assignment_submissions"
    __table_args__ = (
        UniqueConstraint(
            "assignment_id", "student_id", "version_number",
            name="uq_assignment_submission_version",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    repository_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    is_late: Mapped[bool] = mapped_column(default=False, server_default="0", nullable=False)
    late_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AssignmentReworkRequest(Base):
    """Immutable teacher rework authorization, closed by the next submission."""

    __tablename__ = "assignment_rework_requests"
    __table_args__ = (
        UniqueConstraint("assignment_id", "student_id", "request_number", name="uq_assignment_rework_number"),
        CheckConstraint("status IN ('open', 'completed')", name="ck_assignment_rework_status"),
        CheckConstraint("request_type IN ('redo', 'reopen')", name="ck_assignment_rework_type"),
        CheckConstraint("request_type = 'reopen' OR (feedback IS NOT NULL AND due_at IS NOT NULL)", name="ck_assignment_redo_details"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("assignment_submissions.id", ondelete="RESTRICT"), nullable=False)
    request_number: Mapped[int] = mapped_column(Integer, nullable=False)
    request_type: Mapped[str] = mapped_column(String(12), default="redo", server_default="redo", nullable=False)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.current_timestamp(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open", nullable=False)
    completed_submission_id: Mapped[int | None] = mapped_column(ForeignKey("assignment_submissions.id", ondelete="SET NULL"), nullable=True)


class PrivateAssignmentFile(Base):
    """Private file metadata; the backing object is never served as a static asset."""

    __tablename__ = "private_assignment_files"
    __table_args__ = (
        UniqueConstraint("storage_key", name="uq_private_assignment_file_key"),
        CheckConstraint(
            "(assignment_id IS NOT NULL AND submission_id IS NULL AND session_id IS NULL) OR "
            "(assignment_id IS NULL AND submission_id IS NOT NULL AND session_id IS NULL) OR "
            "(assignment_id IS NULL AND submission_id IS NULL AND session_id IS NOT NULL)",
            name="ck_private_file_parent",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int | None] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), nullable=True, index=True)
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("assignment_submissions.id", ondelete="CASCADE"), nullable=True, index=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("attendance_sessions.id", ondelete="CASCADE"), nullable=True, index=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(80), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.current_timestamp(), nullable=False)


class AssignmentGradeVersion(Base):
    """Versioned grading record; drafts are private until published."""

    __tablename__ = "assignment_grade_versions"
    __table_args__ = (
        UniqueConstraint(
            "assignment_id", "student_id", "version_number",
            name="uq_assignment_grade_version",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("assignment_submissions.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    rubric_scores: Mapped[list | None] = mapped_column(JSON, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    internal_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="draft", server_default="draft", nullable=False
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


class RubricTemplate(Base):
    __tablename__ = "rubric_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    criteria: Mapped[list] = mapped_column(JSON, nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), onupdate=func.current_timestamp(), nullable=False
    )


class ClassEnrollment(Base):
    __tablename__ = "class_enrollments"
    __table_args__ = (
        UniqueConstraint("class_id", "student_id", name="uq_class_student"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    class_id: Mapped[int] = mapped_column(
        ForeignKey("training_classes.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    enrolled_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )
    tuition_status: Mapped[str] = mapped_column(
        String(16), default="unpaid", server_default="unpaid", nullable=False
    )
    attendance_status: Mapped[str] = mapped_column(
        String(16), default="not_recorded", server_default="not_recorded", nullable=False
    )
    enrollment_status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active", nullable=False
    )
    effective_date: Mapped[date | None] = mapped_column(nullable=True)
    last_attendance_at: Mapped[datetime | None] = mapped_column(
        DateTime(), nullable=True
    )

    training_class: Mapped["TrainingClass"] = relationship(
        back_populates="enrollments"
    )
    student: Mapped["User"] = relationship(foreign_keys=[student_id])
    enrolled_by: Mapped["User | None"] = relationship(foreign_keys=[enrolled_by_id])
