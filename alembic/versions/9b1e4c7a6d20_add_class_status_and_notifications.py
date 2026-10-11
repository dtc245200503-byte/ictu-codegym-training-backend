"""add class statuses and student notifications

Revision ID: 9b1e4c7a6d20
Revises: 4d7a2e1c9b31
Create Date: 2026-10-11 06:45:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9b1e4c7a6d20"
down_revision: Union[str, Sequence[str], None] = "4d7a2e1c9b31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "class_enrollments",
        sa.Column(
            "tuition_status",
            sa.String(16),
            server_default="unpaid",
            nullable=False,
        ),
    )
    op.add_column(
        "class_enrollments",
        sa.Column(
            "attendance_status",
            sa.String(16),
            server_default="not_recorded",
            nullable=False,
        ),
    )
    op.add_column(
        "class_enrollments",
        sa.Column("last_attendance_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["class_id"], ["training_classes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_id", "notifications", ["id"])
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_index("ix_notifications_id", table_name="notifications")
    op.drop_table("notifications")
    op.drop_column("class_enrollments", "last_attendance_at")
    op.drop_column("class_enrollments", "attendance_status")
    op.drop_column("class_enrollments", "tuition_status")
