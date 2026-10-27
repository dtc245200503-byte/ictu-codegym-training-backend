"""Add manager-approved tuition discount ledger."""
from alembic import op
import sqlalchemy as sa


revision = "b2d6a4e90c71"
down_revision = "f81c3b6d0a42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tuition_discounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Numeric(18, 2), nullable=False),
        sa.Column("amount_vnd", sa.Numeric(18, 0), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="approved"),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("kind IN ('fixed', 'percent')", name="ck_tuition_discount_kind"),
        sa.CheckConstraint("value > 0", name="ck_tuition_discount_value_positive"),
        sa.CheckConstraint("amount_vnd > 0", name="ck_tuition_discount_amount_positive"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_tuition_discount_status"),
        sa.ForeignKeyConstraint(["plan_id"], ["tuition_plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_tuition_discounts_plan_id", "tuition_discounts", ["plan_id"])


def downgrade() -> None:
    op.drop_index("ix_tuition_discounts_plan_id", table_name="tuition_discounts")
    op.drop_table("tuition_discounts")
