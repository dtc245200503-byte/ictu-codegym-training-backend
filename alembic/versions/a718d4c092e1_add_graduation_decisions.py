"""Add graduation eligibility decisions and confirmation history."""
from alembic import op
import sqlalchemy as sa


revision = "a718d4c092e1"
down_revision = "d18c9f40a621"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("study_programs", sa.Column("graduation_attendance_threshold", sa.Numeric(5, 2), nullable=True))
    op.add_column("student_profiles", sa.Column("graduation_status", sa.String(20), nullable=False, server_default="studying"))
    op.create_table(
        "graduation_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("program_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("confirmed_by", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("eligibility_snapshot", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["program_id"], ["study_programs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("student_id", "program_id", name="uq_graduation_student_program"),
    )
    op.create_index("ix_graduation_records_student_id", "graduation_records", ["student_id"])
    op.create_index("ix_graduation_records_program_id", "graduation_records", ["program_id"])
    op.create_table(
        "graduation_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("program_id", sa.Integer(), nullable=False),
        sa.Column("confirmed_by", sa.Integer(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("eligibility_snapshot", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["program_id"], ["study_programs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_graduation_events_student_id", "graduation_events", ["student_id"])
    op.create_index("ix_graduation_events_program_id", "graduation_events", ["program_id"])


def downgrade() -> None:
    op.drop_index("ix_graduation_events_program_id", table_name="graduation_events")
    op.drop_index("ix_graduation_events_student_id", table_name="graduation_events")
    op.drop_table("graduation_events")
    op.drop_index("ix_graduation_records_program_id", table_name="graduation_records")
    op.drop_index("ix_graduation_records_student_id", table_name="graduation_records")
    op.drop_table("graduation_records")
    op.drop_column("student_profiles", "graduation_status")
    op.drop_column("study_programs", "graduation_attendance_threshold")
