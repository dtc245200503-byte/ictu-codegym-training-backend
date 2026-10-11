"""Add Sprint 4 schedule, leave, attendance, and enrollment workflows."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "c4e8a910d672"
down_revision: Union[str, Sequence[str], None] = "b9c2e1f7a450"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("attendance_sessions") as batch_op:
        batch_op.add_column(sa.Column("teacher_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_attendance_sessions_teacher_id_users",
            "users",
            ["teacher_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.add_column(sa.Column("room", sa.String(80), nullable=True))
        batch_op.add_column(sa.Column("lesson_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_attendance_sessions_lesson_id_course_lessons",
            "course_lessons",
            ["lesson_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.add_column(
            sa.Column(
                "is_cancelled",
                sa.Boolean(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "cancellation_reason",
                sa.String(1000),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "conflict_override",
                sa.Boolean(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )

    op.add_column(
        "class_enrollments",
        sa.Column(
            "enrollment_status",
            sa.String(20),
            server_default="active",
            nullable=False,
        ),
    )
    op.add_column(
        "class_enrollments", sa.Column("effective_date", sa.Date(), nullable=True)
    )

    op.create_table(
        "attendance_schedule_holidays",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("holiday_date", sa.Date(), nullable=False, unique=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_table(
        "attendance_change_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("changed_by", sa.Integer(), nullable=True),
        sa.Column("old_status", sa.String(24), nullable=True),
        sa.Column("new_status", sa.String(24), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["record_id"], ["attendance_records.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["changed_by"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_attendance_change_logs_record_id",
        "attendance_change_logs",
        ["record_id"],
    )

    op.create_table(
        "absence_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("evidence_filename", sa.String(255), nullable=True),
        sa.Column("evidence_content_type", sa.String(100), nullable=True),
        sa.Column(
            "evidence_data",
            sa.LargeBinary().with_variant(mysql.LONGBLOB(), "mysql"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("decision_by", sa.Integer(), nullable=True),
        sa.Column("decision_reason", sa.String(1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"], ["attendance_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["decision_by"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("ix_absence_requests_session_id", "absence_requests", ["session_id"])
    op.create_index("ix_absence_requests_student_id", "absence_requests", ["student_id"])

    op.create_table(
        "student_enrollment_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("target_class_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["class_id"], ["training_classes.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_class_id"], ["training_classes.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_student_enrollment_history_student_id",
        "student_enrollment_history",
        ["student_id"],
    )
    op.create_index(
        "ix_student_enrollment_history_class_id",
        "student_enrollment_history",
        ["class_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_student_enrollment_history_class_id",
        table_name="student_enrollment_history",
    )
    op.drop_index(
        "ix_student_enrollment_history_student_id",
        table_name="student_enrollment_history",
    )
    op.drop_table("student_enrollment_history")

    op.drop_index("ix_absence_requests_student_id", table_name="absence_requests")
    op.drop_index("ix_absence_requests_session_id", table_name="absence_requests")
    op.drop_table("absence_requests")

    op.drop_index(
        "ix_attendance_change_logs_record_id", table_name="attendance_change_logs"
    )
    op.drop_table("attendance_change_logs")
    op.drop_table("attendance_schedule_holidays")
    op.drop_column("class_enrollments", "effective_date")
    op.drop_column("class_enrollments", "enrollment_status")
    with op.batch_alter_table("attendance_sessions") as batch_op:
        batch_op.drop_constraint(
            "fk_attendance_sessions_lesson_id_course_lessons",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_attendance_sessions_teacher_id_users",
            type_="foreignkey",
        )
        batch_op.drop_column("lesson_id")
        batch_op.drop_column("room")
        batch_op.drop_column("is_cancelled")
        batch_op.drop_column("cancellation_reason")
        batch_op.drop_column("conflict_override")
        batch_op.drop_column("teacher_id")
