"""Add immutable tuition plans and installment schedules."""
from alembic import op
import sqlalchemy as sa


revision = "e7b3105a2c44"
down_revision = "d9a4e1c672b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tuition_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enrollment_id", sa.Integer(), nullable=False),
        sa.Column("fee_schedule_id", sa.Integer(), nullable=False),
        sa.Column("total_due_vnd", sa.Numeric(18, 0), nullable=False),
        sa.Column("applied_on", sa.Date(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint("total_due_vnd >= 0", name="ck_tuition_plan_nonnegative"),
        sa.ForeignKeyConstraint(
            ["enrollment_id"], ["class_enrollments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["fee_schedule_id"], ["program_fee_schedules.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("enrollment_id", name="uq_tuition_plan_enrollment"),
    )
    op.create_table(
        "tuition_installments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("installment_number", sa.Integer(), nullable=False),
        sa.Column("amount_due_vnd", sa.Numeric(18, 0), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.CheckConstraint("amount_due_vnd > 0", name="ck_tuition_installment_positive"),
        sa.ForeignKeyConstraint(["plan_id"], ["tuition_plans.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "plan_id", "installment_number", name="uq_tuition_installment_number"
        ),
    )
    op.create_index(
        "ix_tuition_installments_plan_id", "tuition_installments", ["plan_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_tuition_installments_plan_id", table_name="tuition_installments")
    op.drop_table("tuition_installments")
    op.drop_table("tuition_plans")
