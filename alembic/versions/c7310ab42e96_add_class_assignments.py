"""Add class assignments for students."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7310ab42e96"
down_revision: Union[str, Sequence[str], None] = "9b1e4c7a6d20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["class_id"], ["training_classes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assignments_id", "assignments", ["id"])
    op.create_index("ix_assignments_class_id", "assignments", ["class_id"])


def downgrade() -> None:
    op.drop_index("ix_assignments_class_id", table_name="assignments")
    op.drop_index("ix_assignments_id", table_name="assignments")
    op.drop_table("assignments")
