"""Add reusable rubric templates and immutable assignment snapshots."""
from alembic import op
import sqlalchemy as sa


revision = "a62d4ef901bc"
down_revision = "f17c2b8a640d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rubric_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("criteria", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.add_column("assignments", sa.Column("rubric_snapshot", sa.JSON(), nullable=True))
    op.add_column("assignment_grade_versions", sa.Column("rubric_scores", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("assignment_grade_versions", "rubric_scores")
    op.drop_column("assignments", "rubric_snapshot")
    op.drop_table("rubric_templates")
