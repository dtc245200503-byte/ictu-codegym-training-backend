"""Add assignment late-submission policy and immutable late metadata."""
from alembic import op
import sqlalchemy as sa


revision = "c3b7e1290a64"
down_revision = "b06c2e8a41d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("assignments") as batch:
        batch.add_column(sa.Column("late_submission_policy", sa.String(12), nullable=True))
        batch.create_check_constraint("ck_assignment_late_policy",
            "late_submission_policy IS NULL OR late_submission_policy IN ('reject', 'accept')")
    op.add_column("assignment_submissions", sa.Column("is_late", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("assignment_submissions", sa.Column("late_hours", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("assignment_submissions", "late_hours")
    op.drop_column("assignment_submissions", "is_late")
    with op.batch_alter_table("assignments") as batch:
        batch.drop_constraint("ck_assignment_late_policy", type_="check")
        batch.drop_column("late_submission_policy")
