from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ClassSurvey(Base):
    __tablename__ = "class_surveys"
    __table_args__ = (UniqueConstraint("class_id", name="uq_class_survey_one_per_class"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("training_classes.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    deadline: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.current_timestamp(), nullable=False)


class SurveyQuestion(Base):
    __tablename__ = "survey_questions"
    __table_args__ = (UniqueConstraint("survey_id", "position", name="uq_survey_question_position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("class_surveys.id", ondelete="CASCADE"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt: Mapped[str] = mapped_column(String(1000), nullable=False)
    question_type: Mapped[str] = mapped_column(String(16), nullable=False)
    is_required: Mapped[bool] = mapped_column(default=True, nullable=False)
    scale_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scale_max: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AnonymousSurveyResponse(Base):
    """Response payload contains no student ID or identity-bearing foreign key."""

    __tablename__ = "anonymous_survey_responses"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("class_surveys.id", ondelete="CASCADE"), nullable=False, index=True)
    answers: Mapped[dict] = mapped_column(JSON, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.current_timestamp(), nullable=False)


class SurveyRespondentReceipt(Base):
    """Separate reminder ledger; deliberately has no response ID/key."""

    __tablename__ = "survey_respondent_receipts"
    __table_args__ = (UniqueConstraint("survey_id", "student_id", name="uq_survey_student_once"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("class_surveys.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.current_timestamp(), nullable=False)
