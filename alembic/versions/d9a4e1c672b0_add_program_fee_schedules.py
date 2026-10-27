"""Add immutable effective-dated program fee schedules."""
from alembic import op
import sqlalchemy as sa


revision = "d9a4e1c672b0"
down_revision = "a62d4ef901bc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "program_fee_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("program_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("amount_vnd", sa.Numeric(18, 0), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint("amount_vnd >= 0", name="ck_program_fee_nonnegative"),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from <= valid_to",
            name="ck_program_fee_valid_range",
        ),
        sa.ForeignKeyConstraint(
            ["program_id"], ["study_programs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "program_id", "version", name="uq_program_fee_schedule_version"
        ),
    )
    op.create_index(
        "ix_program_fee_effective",
        "program_fee_schedules",
        ["program_id", "valid_from", "valid_to"],
    )


def downgrade() -> None:
    op.drop_index("ix_program_fee_effective", table_name="program_fee_schedules")
    op.drop_table("program_fee_schedules")
