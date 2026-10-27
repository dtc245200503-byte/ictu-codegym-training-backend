from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150), index=True)
    department: Mapped[str] = mapped_column(String(100), index=True)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    session_count: Mapped[int] = mapped_column(
        Integer, default=16, server_default="16", nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    learning_outcomes: Mapped[str | None] = mapped_column(Text, nullable=True)
    prerequisite_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )

    prerequisite: Mapped["Course | None"] = relationship(
        remote_side="Course.id", foreign_keys=[prerequisite_id]
    )
    study_programs: Mapped[list["StudyProgram"]] = relationship(
        "StudyProgram",
        secondary="program_subjects",
        back_populates="courses",
        primaryjoin="Course.id == program_subjects.c.subject_id",
        secondaryjoin="StudyProgram.id == program_subjects.c.program_id",
    )
