"""Track whether a study program accepts new classes."""
from alembic import op
import sqlalchemy as sa


revision = "j8d3e10b52cf"
down_revision = "i7c2d90a41be"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("study_programs", sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column("study_programs", sa.Column("standard_tuition_vnd", sa.Numeric(18, 0), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("study_programs", "standard_tuition_vnd")
    op.drop_column("study_programs", "is_active")
