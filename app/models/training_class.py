from datetime import date, datetime, time

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Index,
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


class Assignment(Base):
    __tablename__ = "assignments"

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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )

    training_class: Mapped["TrainingClass"] = relationship()
    creator: Mapped["User | None"] = relationship()


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
