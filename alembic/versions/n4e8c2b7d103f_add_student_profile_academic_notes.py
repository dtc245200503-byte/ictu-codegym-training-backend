"""Store student entry level and academic profile notes."""
from alembic import op
import sqlalchemy as sa


revision = "n4e8c2b7d103f"
down_revision = "m3b7d1a9f052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("student_profiles", sa.Column("entry_level", sa.String(length=150), nullable=True))
    op.add_column("student_profiles", sa.Column("notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("student_profiles", "notes")
    op.drop_column("student_profiles", "entry_level")
