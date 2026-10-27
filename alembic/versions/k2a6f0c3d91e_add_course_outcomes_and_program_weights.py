"""Store course outcomes and program-specific curriculum weights."""
from alembic import op
import sqlalchemy as sa


revision = "k2a6f0c3d91e"
down_revision = "j8d3e10b52cf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("courses", sa.Column("learning_outcomes", sa.Text(), nullable=True))
    op.add_column("program_subjects", sa.Column("weight", sa.Numeric(8, 2), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("program_subjects", "weight")
    op.drop_column("courses", "learning_outcomes")
