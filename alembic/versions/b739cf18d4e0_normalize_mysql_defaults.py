"""Normalize legacy timestamp and boolean defaults for MySQL."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b739cf18d4e0"
down_revision: Union[str, Sequence[str], None] = "c7310ab42e96"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return

    for table, column in (
        ("users", "created_at"),
        ("users", "updated_at"),
        ("auth_sessions", "created_at"),
        ("password_reset_tokens", "created_at"),
    ):
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(),
            type_=sa.DateTime(),
            existing_nullable=False,
            existing_server_default=sa.text("now()"),
            server_default=sa.text("CURRENT_TIMESTAMP"),
        )

    op.alter_column(
        "users",
        "is_active",
        existing_type=sa.Boolean(),
        type_=sa.Boolean(),
        existing_nullable=False,
        server_default=sa.text("1"),
    )
    op.alter_column(
        "users",
        "is_locked",
        existing_type=sa.Boolean(),
        type_=sa.Boolean(),
        existing_nullable=False,
        server_default=sa.text("0"),
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return

    op.alter_column(
        "users",
        "is_locked",
        existing_type=sa.Boolean(),
        type_=sa.Boolean(),
        existing_nullable=False,
        server_default=None,
    )
    op.alter_column(
        "users",
        "is_active",
        existing_type=sa.Boolean(),
        type_=sa.Boolean(),
        existing_nullable=False,
        server_default=None,
    )
