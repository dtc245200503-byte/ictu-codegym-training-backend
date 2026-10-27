"""Persist planned cohort launch details and copied curriculum."""
from alembic import op
import sqlalchemy as sa


revision = "q7b1e5c3d064"
down_revision = "p6a0e4b2d953"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("training_classes", sa.Column("planned_start_date", sa.Date(), nullable=True))
    op.add_column("training_classes", sa.Column("delivery_mode", sa.String(length=20), server_default="onsite", nullable=False))
    op.add_column("training_classes", sa.Column("program_curriculum_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("training_classes", "program_curriculum_snapshot")
    op.drop_column("training_classes", "delivery_mode")
    op.drop_column("training_classes", "planned_start_date")
