"""Add optional maximum score to assignments."""
from alembic import op
import sqlalchemy as sa


revision = "c20de5af9b31"
down_revision = "8b3e9d41a7c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assignments",
        sa.Column("max_score", sa.Numeric(precision=18, scale=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assignments", "max_score")
