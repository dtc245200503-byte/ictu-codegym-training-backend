from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Index,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


program_subjects = Table(
    "program_subjects",
    Base.metadata,
    Column(
        "program_id",
        ForeignKey("study_programs.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "subject_id",
        ForeignKey("courses.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("order_index", Integer, nullable=False, default=0, server_default="0"),
    Column("weight", Numeric(8, 2), nullable=False, default=Decimal("0"), server_default="0"),
    Column(
        "prerequisite_subject_id",
        ForeignKey("courses.id", ondelete="SET NULL"),
        nullable=True,
    ),
)


class StudyProgram(Base):
    __tablename__ = "study_programs"
    __table_args__ = (CheckConstraint("absence_threshold_percent BETWEEN 0 AND 100", name="ck_program_absence_threshold"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    degree_level: Mapped[str] = mapped_column(
        String(30), default="Đại học", server_default="Đại học"
    )
    total_credits: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_semesters: Mapped[int] = mapped_column(
        Integer, default=8, server_default="8"
    )
    standard_tuition_vnd: Mapped[Decimal] = mapped_column(
        Numeric(18, 0), default=Decimal("0"), server_default="0", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(default=True, server_default="1", nullable=False)
    graduation_attendance_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    absence_threshold_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("20.00"), server_default="20.00", nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    courses: Mapped[list["Course"]] = relationship(
        "Course",
        secondary=program_subjects,
        back_populates="study_programs",
        primaryjoin="StudyProgram.id == program_subjects.c.program_id",
        secondaryjoin="Course.id == program_subjects.c.subject_id",
        order_by=program_subjects.c.order_index,
    )


class ProgramFeeSchedule(Base):
    """Immutable VND fee schedule revision for a study program."""

    __tablename__ = "program_fee_schedules"
    __table_args__ = (
        UniqueConstraint(
            "program_id", "version", name="uq_program_fee_schedule_version"
        ),
        CheckConstraint("amount_vnd >= 0", name="ck_program_fee_nonnegative"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from <= valid_to",
            name="ck_program_fee_valid_range",
        ),
        Index("ix_program_fee_effective", "program_id", "valid_from", "valid_to"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int] = mapped_column(
        ForeignKey("study_programs.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_vnd: Mapped[Decimal] = mapped_column(Numeric(18, 0), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )
