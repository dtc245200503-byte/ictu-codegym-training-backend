from sqlalchemy import Column, ForeignKey, Integer, String, Table, Text
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
    Column(
        "prerequisite_subject_id",
        ForeignKey("courses.id", ondelete="SET NULL"),
        nullable=True,
    ),
)


class StudyProgram(Base):
    __tablename__ = "study_programs"

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
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    courses: Mapped[list["Course"]] = relationship(
        "Course",
        secondary=program_subjects,
        back_populates="study_programs",
        primaryjoin="StudyProgram.id == program_subjects.c.program_id",
        secondaryjoin="Course.id == program_subjects.c.subject_id",
        order_by=program_subjects.c.order_index,
    )
