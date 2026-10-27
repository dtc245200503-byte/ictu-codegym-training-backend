"""Add append-only repository-link assignment submissions."""
from alembic import op
import sqlalchemy as sa


revision = "e31b7f4a2c09"
down_revision = "c20de5af9b31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assignment_submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assignment_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("repository_url", sa.String(length=2048), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "assignment_id", "student_id", "version_number",
            name="uq_assignment_submission_version",
        ),
    )
    op.create_index(
        "ix_assignment_submissions_assignment_id",
        "assignment_submissions",
        ["assignment_id"],
    )
    op.create_index(
        "ix_assignment_submissions_student_id",
        "assignment_submissions",
        ["student_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_assignment_submissions_student_id", table_name="assignment_submissions")
    op.drop_index("ix_assignment_submissions_assignment_id", table_name="assignment_submissions")
    op.drop_table("assignment_submissions")
