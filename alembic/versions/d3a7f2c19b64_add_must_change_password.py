"""Add the first-login password change requirement."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d3a7f2c19b64"
down_revision: Union[str, Sequence[str], None] = "c82e7a3b1d90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
