"""Add assignment draft publishing and recipient snapshots."""
from alembic import op
import sqlalchemy as sa


revision = "8b3e9d41a7c2"
down_revision = "6f2a91c84d30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = (
        sa.Column("status", sa.String(length=20), server_default="published", nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("roster_snapshotted_at", sa.DateTime(), nullable=True),
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("assignments") as batch_op:
            for column in columns:
                batch_op.add_column(column)
            batch_op.create_foreign_key(
                "fk_assignments_session_id_attendance_sessions",
                "attendance_sessions",
                ["session_id"],
                ["id"],
                ondelete="SET NULL",
            )
    else:
        for column in columns:
            op.add_column("assignments", column)
        op.create_foreign_key(
            "fk_assignments_session_id_attendance_sessions",
            "assignments",
            "attendance_sessions",
            ["session_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_assignments_session_id", "assignments", ["session_id"])
    op.create_table(
        "assignment_recipients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assignment_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column(
            "added_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"], ["assignments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("assignment_id", "student_id", name="uq_assignment_recipient"),
    )
    op.create_index(
        "ix_assignment_recipients_assignment_id",
        "assignment_recipients",
        ["assignment_id"],
    )
    op.create_index(
        "ix_assignment_recipients_student_id",
        "assignment_recipients",
        ["student_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_assignment_recipients_student_id", table_name="assignment_recipients")
    op.drop_index("ix_assignment_recipients_assignment_id", table_name="assignment_recipients")
    op.drop_table("assignment_recipients")
    op.drop_index("ix_assignments_session_id", table_name="assignments")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("assignments") as batch_op:
            batch_op.drop_constraint(
                "fk_assignments_session_id_attendance_sessions", type_="foreignkey"
            )
            batch_op.drop_column("roster_snapshotted_at")
            batch_op.drop_column("session_id")
            batch_op.drop_column("status")
    else:
        op.drop_constraint(
            "fk_assignments_session_id_attendance_sessions",
            "assignments",
            type_="foreignkey",
        )
        op.drop_column("assignments", "roster_snapshotted_at")
        op.drop_column("assignments", "session_id")
        op.drop_column("assignments", "status")
