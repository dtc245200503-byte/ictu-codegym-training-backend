"""Persist privacy-preserving public lead submission limits."""
from alembic import op
import sqlalchemy as sa


revision = "l5c8e1d4a027"
down_revision = "k2a6f0c3d91e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_submission_rate_limits",
        sa.Column("client_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("client_fingerprint", "window_started_at"),
    )
    op.create_index("ix_lead_rate_window_started", "lead_submission_rate_limits", ["window_started_at"])


def downgrade() -> None:
    op.drop_index("ix_lead_rate_window_started", table_name="lead_submission_rate_limits")
    op.drop_table("lead_submission_rate_limits")
