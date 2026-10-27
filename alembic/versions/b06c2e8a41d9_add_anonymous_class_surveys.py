"""Add class surveys with separated respondent receipts and anonymous answers."""
from alembic import op
import sqlalchemy as sa


revision = "b06c2e8a41d9"
down_revision = "a718d4c092e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "class_surveys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("deadline", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.ForeignKeyConstraint(["class_id"], ["training_classes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("class_id", name="uq_class_survey_one_per_class"),
    )
    op.create_index("ix_class_surveys_class_id", "class_surveys", ["class_id"])
    op.create_table(
        "survey_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("survey_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("prompt", sa.String(1000), nullable=False),
        sa.Column("question_type", sa.String(16), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("scale_min", sa.Integer(), nullable=True),
        sa.Column("scale_max", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["survey_id"], ["class_surveys.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("survey_id", "position", name="uq_survey_question_position"),
    )
    op.create_index("ix_survey_questions_survey_id", "survey_questions", ["survey_id"])
    op.create_table(
        "anonymous_survey_responses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("survey_id", sa.Integer(), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.ForeignKeyConstraint(["survey_id"], ["class_surveys.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_anonymous_survey_responses_survey_id", "anonymous_survey_responses", ["survey_id"])
    op.create_table(
        "survey_respondent_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("survey_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.ForeignKeyConstraint(["survey_id"], ["class_surveys.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("survey_id", "student_id", name="uq_survey_student_once"),
    )
    op.create_index("ix_survey_respondent_receipts_survey_id", "survey_respondent_receipts", ["survey_id"])
    op.create_index("ix_survey_respondent_receipts_student_id", "survey_respondent_receipts", ["student_id"])


def downgrade() -> None:
    op.drop_index("ix_survey_respondent_receipts_student_id", table_name="survey_respondent_receipts")
    op.drop_index("ix_survey_respondent_receipts_survey_id", table_name="survey_respondent_receipts")
    op.drop_table("survey_respondent_receipts")
    op.drop_index("ix_anonymous_survey_responses_survey_id", table_name="anonymous_survey_responses")
    op.drop_table("anonymous_survey_responses")
    op.drop_index("ix_survey_questions_survey_id", table_name="survey_questions")
    op.drop_table("survey_questions")
    op.drop_index("ix_class_surveys_class_id", table_name="class_surveys")
    op.drop_table("class_surveys")
