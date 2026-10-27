"""Add multi-class announcements and recipient notification linkage."""
from alembic import op
import sqlalchemy as sa


revision = "g31a9e5c2d70"
down_revision = "f30a7c9d1e42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
    )
    op.create_table(
        "announcement_classes",
        sa.Column("announcement_id", sa.Integer(), sa.ForeignKey("announcements.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("training_classes.id", ondelete="CASCADE"), primary_key=True),
    )
    with op.batch_alter_table("notifications") as batch:
        batch.add_column(sa.Column("announcement_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("navigation_url", sa.String(500), nullable=True))
        batch.create_foreign_key("fk_notifications_announcement_id", "announcements", ["announcement_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_notifications_announcement_id", "notifications", ["announcement_id"])
    op.create_table(
        "attendance_risk_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("training_classes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_at_risk", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("absent_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completed_session_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("absence_threshold_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("activation_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("flagged_at", sa.DateTime()),
        sa.Column("cleared_at", sa.DateTime()),
        sa.Column("last_evaluated_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.UniqueConstraint("class_id", "student_id", name="uq_attendance_risk_class_student"),
        sa.CheckConstraint("absence_threshold_percent BETWEEN 0 AND 100", name="ck_attendance_risk_threshold"),
    )
    op.create_index("ix_attendance_risk_states_class_id", "attendance_risk_states", ["class_id"])
    op.create_index("ix_attendance_risk_states_student_id", "attendance_risk_states", ["student_id"])
    op.create_table(
        "session_learning_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("attendance_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
    )
    op.create_index("ix_session_learning_links_session_id", "session_learning_links", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_session_learning_links_session_id", table_name="session_learning_links")
    op.drop_table("session_learning_links")
    op.drop_index("ix_attendance_risk_states_student_id", table_name="attendance_risk_states")
    op.drop_index("ix_attendance_risk_states_class_id", table_name="attendance_risk_states")
    op.drop_table("attendance_risk_states")
    op.drop_index("ix_notifications_announcement_id", table_name="notifications")
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("fk_notifications_announcement_id", type_="foreignkey")
        batch.drop_column("navigation_url")
        batch.drop_column("announcement_id")
    op.drop_table("announcement_classes")
    op.drop_table("announcements")
