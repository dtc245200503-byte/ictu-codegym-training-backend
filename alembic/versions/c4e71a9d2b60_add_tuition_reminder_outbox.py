"""Add deduplicated tuition reminder outbox."""
from alembic import op
import sqlalchemy as sa


revision = "c4e71a9d2b60"
down_revision = "b2d6a4e90c71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tuition_reminder_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("installment_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("notification_id", sa.Integer(), nullable=True),
        sa.Column("reminder_type", sa.String(length=16), nullable=False),
        sa.Column("reminder_date", sa.Date(), nullable=False),
        sa.Column("recipient_email", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("reminder_type IN ('due_soon', 'overdue')", name="ck_tuition_reminder_type"),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name="ck_tuition_reminder_status"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_tuition_reminder_attempts"),
        sa.ForeignKeyConstraint(["installment_id"], ["tuition_installments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("installment_id", "reminder_type", "reminder_date", name="uq_tuition_reminder_installment_type_date"),
    )
    op.create_index("ix_tuition_reminder_outbox_installment_id", "tuition_reminder_outbox", ["installment_id"])
    op.create_index("ix_tuition_reminder_outbox_student_id", "tuition_reminder_outbox", ["student_id"])


def downgrade() -> None:
    op.drop_index("ix_tuition_reminder_outbox_student_id", table_name="tuition_reminder_outbox")
    op.drop_index("ix_tuition_reminder_outbox_installment_id", table_name="tuition_reminder_outbox")
    op.drop_table("tuition_reminder_outbox")
