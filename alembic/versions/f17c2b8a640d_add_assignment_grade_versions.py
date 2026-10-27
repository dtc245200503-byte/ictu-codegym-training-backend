"""Add draft and published assignment grade versions."""
from alembic import op
import sqlalchemy as sa


revision = "f17c2b8a640d"
down_revision = "e31b7f4a2c09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assignment_grade_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assignment_id", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("internal_comment", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["submission_id"], ["assignment_submissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "assignment_id", "student_id", "version_number",
            name="uq_assignment_grade_version",
        ),
    )
    op.create_index(
        "ix_assignment_grade_versions_assignment_id",
        "assignment_grade_versions",
        ["assignment_id"],
    )
    op.create_index(
        "ix_assignment_grade_versions_student_id",
        "assignment_grade_versions",
        ["student_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_assignment_grade_versions_student_id", table_name="assignment_grade_versions")
    op.drop_index("ix_assignment_grade_versions_assignment_id", table_name="assignment_grade_versions")
    op.drop_table("assignment_grade_versions")
