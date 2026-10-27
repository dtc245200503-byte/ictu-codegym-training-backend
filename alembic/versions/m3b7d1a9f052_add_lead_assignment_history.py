"""Record lead assignment changes for admissions handoff history."""
from alembic import op
import sqlalchemy as sa


revision = "m3b7d1a9f052"
down_revision = "l5c8e1d4a027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_assignment_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("previous_assignee_id", sa.Integer(), nullable=True),
        sa.Column("new_assignee_id", sa.Integer(), nullable=True),
        sa.Column("assigned_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["previous_assignee_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["new_assignee_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lead_assignment_history_lead_created", "lead_assignment_history", ["lead_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_lead_assignment_history_lead_created", table_name="lead_assignment_history")
    op.drop_table("lead_assignment_history")
