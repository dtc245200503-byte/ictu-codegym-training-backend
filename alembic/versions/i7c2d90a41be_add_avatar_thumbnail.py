"""Store a generated private avatar thumbnail."""
from alembic import op
import sqlalchemy as sa


revision = "i7c2d90a41be"
down_revision = "h61a4c9e2b30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_avatars", sa.Column("thumbnail_data", sa.LargeBinary(length=262144), nullable=True))


def downgrade() -> None:
    op.drop_column("user_avatars", "thumbnail_data")
