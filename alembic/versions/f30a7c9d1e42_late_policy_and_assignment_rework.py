"""Use confirmed late policy values and store teacher rework requests."""
from alembic import op
import sqlalchemy as sa


revision = "f30a7c9d1e42"
down_revision = "d4c9e1a73b20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE assignments SET late_submission_policy = 'ALLOW_LATE' WHERE late_submission_policy = 'accept'")
    op.execute("UPDATE assignments SET late_submission_policy = 'BLOCK_LATE' WHERE late_submission_policy IS NULL OR late_submission_policy = 'reject'")
    with op.batch_alter_table("assignments") as batch:
        batch.drop_constraint("ck_assignment_late_policy", type_="check")
        batch.alter_column("late_submission_policy", existing_type=sa.String(12), type_=sa.String(16), nullable=False, server_default="BLOCK_LATE")
        batch.create_check_constraint("ck_assignment_late_policy", "late_submission_policy IN ('BLOCK_LATE', 'ALLOW_LATE')")
    op.create_table(
        "assignment_rework_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assignment_id", sa.Integer(), sa.ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("assignment_submissions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_number", sa.Integer(), nullable=False),
        sa.Column("request_type", sa.String(12), server_default="redo", nullable=False),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("requested_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("status", sa.String(16), server_default="open", nullable=False),
        sa.Column("completed_submission_id", sa.Integer(), sa.ForeignKey("assignment_submissions.id", ondelete="SET NULL")),
        sa.UniqueConstraint("assignment_id", "student_id", "request_number", name="uq_assignment_rework_number"),
        sa.CheckConstraint("status IN ('open', 'completed')", name="ck_assignment_rework_status"),
        sa.CheckConstraint("request_type IN ('redo', 'reopen')", name="ck_assignment_rework_type"),
        sa.CheckConstraint("request_type = 'reopen' OR (feedback IS NOT NULL AND due_at IS NOT NULL)", name="ck_assignment_redo_details"),
    )
    op.create_index("ix_assignment_rework_requests_assignment_id", "assignment_rework_requests", ["assignment_id"])
    op.create_index("ix_assignment_rework_requests_student_id", "assignment_rework_requests", ["student_id"])
    op.create_table(
        "private_assignment_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("assignment_submissions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("attendance_sessions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(80), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.UniqueConstraint("storage_key", name="uq_private_assignment_file_key"),
        sa.CheckConstraint("(submission_id IS NOT NULL AND session_id IS NULL) OR (submission_id IS NULL AND session_id IS NOT NULL)", name="ck_private_file_parent"),
    )
    op.create_index("ix_private_assignment_files_submission_id", "private_assignment_files", ["submission_id"])
    op.create_index("ix_private_assignment_files_session_id", "private_assignment_files", ["session_id"])
    op.add_column("attendance_sessions", sa.Column("materials_open_early", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("study_programs", sa.Column("absence_threshold_percent", sa.Numeric(5, 2), server_default="20.00", nullable=False))
    with op.batch_alter_table("study_programs") as batch:
        batch.create_check_constraint("ck_program_absence_threshold", "absence_threshold_percent BETWEEN 0 AND 100")
    op.add_column("student_profiles", sa.Column("class_reminder_opt_out", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("notification_id", sa.Integer(), sa.ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("attendance_sessions.id", ondelete="CASCADE")),
        sa.Column("dedupe_key", sa.String(180), nullable=False),
        sa.Column("recipient_email", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(500)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("sent_at", sa.DateTime()),
        sa.UniqueConstraint("dedupe_key", name="uq_notification_outbox_dedupe"),
        sa.UniqueConstraint("notification_id", name="uq_notification_outbox_notification"),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name="ck_notification_outbox_status"),
        sa.CheckConstraint("attempt_count BETWEEN 0 AND 3", name="ck_notification_outbox_attempts"),
    )
    op.create_index("ix_notification_outbox_session_id", "notification_outbox", ["session_id"])


def downgrade() -> None:
    with op.batch_alter_table("study_programs") as batch:
        batch.drop_constraint("ck_program_absence_threshold", type_="check")
    op.drop_column("study_programs", "absence_threshold_percent")
    op.drop_column("attendance_sessions", "materials_open_early")
    op.drop_index("ix_notification_outbox_session_id", table_name="notification_outbox")
    op.drop_table("notification_outbox")
    op.drop_column("student_profiles", "class_reminder_opt_out")
    op.drop_index("ix_private_assignment_files_session_id", table_name="private_assignment_files")
    op.drop_index("ix_private_assignment_files_submission_id", table_name="private_assignment_files")
    op.drop_table("private_assignment_files")
    op.drop_index("ix_assignment_rework_requests_student_id", table_name="assignment_rework_requests")
    op.drop_index("ix_assignment_rework_requests_assignment_id", table_name="assignment_rework_requests")
    op.drop_table("assignment_rework_requests")
    op.execute("UPDATE assignments SET late_submission_policy = 'accept' WHERE late_submission_policy = 'ALLOW_LATE'")
    op.execute("UPDATE assignments SET late_submission_policy = 'reject' WHERE late_submission_policy = 'BLOCK_LATE'")
    with op.batch_alter_table("assignments") as batch:
        batch.drop_constraint("ck_assignment_late_policy", type_="check")
        batch.alter_column("late_submission_policy", existing_type=sa.String(16), type_=sa.String(12), nullable=True, server_default=None)
        batch.create_check_constraint("ck_assignment_late_policy", "late_submission_policy IS NULL OR late_submission_policy IN ('reject', 'accept')")
