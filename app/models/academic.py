from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class GradePolicy(Base):
    """Versioned course grade policy with an optional per-class override."""

    __tablename__ = "grade_policies"
    __table_args__ = (
        UniqueConstraint(
            "program_id", "course_id", "scope_key", "version",
            name="uq_grade_policy_scope_version",
        ),
        CheckConstraint("pass_score BETWEEN 0 AND 100", name="ck_grade_policy_pass_score"),
        CheckConstraint(
            "critical_score IS NULL OR critical_score BETWEEN 0 AND 100",
            name="ck_grade_policy_critical_score",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int] = mapped_column(
        ForeignKey("study_programs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    class_id: Mapped[int | None] = mapped_column(
        ForeignKey("training_classes.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    scope_key: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    components: Mapped[list] = mapped_column(JSON, nullable=False)
    pass_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    critical_component_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    critical_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )


class CourseFinalGrade(Base):
    """Immutable calculation snapshot; reopening changes only lock metadata."""

    __tablename__ = "course_final_grades"
    __table_args__ = (
        UniqueConstraint(
            "class_id", "student_id", "course_id", "version",
            name="uq_course_final_grade_version",
        ),
        CheckConstraint(
            "result IN ('incomplete', 'pass', 'fail')", name="ck_course_final_grade_result"
        ),
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_course_final_grade_score"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(
        ForeignKey("training_classes.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("grade_policies.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    breakdown: Mapped[dict] = mapped_column(JSON, nullable=False)
    is_locked: Mapped[bool] = mapped_column(default=True, nullable=False)
    finalized_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    finalized_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )
    reopened_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    reopen_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class GraduationRecord(Base):
    """Current graduation decision for one student in one program."""

    __tablename__ = "graduation_records"
    __table_args__ = (UniqueConstraint("student_id", "program_id", name="uq_graduation_student_program"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("study_programs.id", ondelete="RESTRICT"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_graduated")
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    eligibility_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)


class GraduationEvent(Base):
    """Append-only confirmation history, including each batch decision."""

    __tablename__ = "graduation_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("study_programs.id", ondelete="RESTRICT"), nullable=False, index=True)
    confirmed_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.current_timestamp(), nullable=False)
    eligibility_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
