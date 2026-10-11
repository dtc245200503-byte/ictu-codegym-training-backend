"""Add degree programs and session-based attendance history."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a541c02e7f34"
down_revision: Union[str, Sequence[str], None] = "b739cf18d4e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "study_programs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column(
            "degree_level",
            sa.String(length=30),
            server_default="Đại học",
            nullable=False,
        ),
        sa.Column("total_credits", sa.Integer(), nullable=False),
        sa.Column(
            "duration_semesters",
            sa.Integer(),
            server_default=sa.text("8"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_study_programs_id", "study_programs", ["id"])
    op.create_index("ix_study_programs_code", "study_programs", ["code"])
    op.create_index("ix_study_programs_name", "study_programs", ["name"])

    op.create_table(
        "program_courses",
        sa.Column("program_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["program_id"], ["study_programs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("program_id", "course_id"),
    )

    op.create_table(
        "attendance_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["class_id"], ["training_classes.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("class_id", "session_date", name="uq_class_session_date"),
    )
    op.create_index("ix_attendance_sessions_id", "attendance_sessions", ["id"])
    op.create_index(
        "ix_attendance_sessions_class_id", "attendance_sessions", ["class_id"]
    )

    op.create_table(
        "attendance_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("marked_by", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "marked_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["attendance_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["marked_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "student_id", name="uq_session_student"),
    )
    op.create_index("ix_attendance_records_id", "attendance_records", ["id"])
    op.create_index(
        "ix_attendance_records_session_id", "attendance_records", ["session_id"]
    )
    op.create_index(
        "ix_attendance_records_student_id", "attendance_records", ["student_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_attendance_records_student_id", table_name="attendance_records")
    op.drop_index("ix_attendance_records_session_id", table_name="attendance_records")
    op.drop_index("ix_attendance_records_id", table_name="attendance_records")
    op.drop_table("attendance_records")
    op.drop_index("ix_attendance_sessions_class_id", table_name="attendance_sessions")
    op.drop_index("ix_attendance_sessions_id", table_name="attendance_sessions")
    op.drop_table("attendance_sessions")
    op.drop_table("program_courses")
    op.drop_index("ix_study_programs_name", table_name="study_programs")
    op.drop_index("ix_study_programs_code", table_name="study_programs")
    op.drop_index("ix_study_programs_id", table_name="study_programs")
    op.drop_table("study_programs")
