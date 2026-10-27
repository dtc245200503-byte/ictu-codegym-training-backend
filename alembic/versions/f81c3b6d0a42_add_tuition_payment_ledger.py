"""Add append-only tuition payments and reversals."""
from alembic import op
import sqlalchemy as sa


revision = "f81c3b6d0a42"
down_revision = "e7b3105a2c44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tuition_payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("installment_id", sa.Integer(), nullable=False),
        sa.Column("receipt_code", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("amount_vnd", sa.Numeric(18, 0), nullable=False),
        sa.Column("method", sa.String(length=20), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("collected_by", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint("amount_vnd > 0", name="ck_tuition_payment_positive"),
        sa.ForeignKeyConstraint(
            ["installment_id"], ["tuition_installments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["collected_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("receipt_code", name="uq_tuition_payment_receipt"),
        sa.UniqueConstraint(
            "installment_id", "idempotency_key", name="uq_tuition_payment_idempotency"
        ),
    )
    op.create_index(
        "ix_tuition_payments_installment_id",
        "tuition_payments",
        ["installment_id"],
    )
    op.create_table(
        "tuition_payment_reversals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("amount_vnd", sa.Numeric(18, 0), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("reversed_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint("amount_vnd > 0", name="ck_tuition_reversal_positive"),
        sa.ForeignKeyConstraint(
            ["payment_id"], ["tuition_payments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["reversed_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("payment_id", name="uq_tuition_payment_single_reversal"),
        sa.UniqueConstraint(
            "payment_id", "idempotency_key", name="uq_tuition_reversal_idempotency"
        ),
    )


def downgrade() -> None:
    op.drop_table("tuition_payment_reversals")
    op.drop_index("ix_tuition_payments_installment_id", table_name="tuition_payments")
    op.drop_table("tuition_payments")
