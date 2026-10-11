"""Add course syllabus and lead assignment/callback support."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a71c4e902d5f"
down_revision: Union[str, Sequence[str], None] = "f4b6c2d9a130"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch_op:
        batch_op.add_column(sa.Column("assigned_to_user_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            "ix_leads_assigned_to_user_id", ["assigned_to_user_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_leads_assigned_to_user_id_users",
            "users",
            ["assigned_to_user_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("lead_activities") as batch_op:
        batch_op.add_column(sa.Column("scheduled_for", sa.DateTime(), nullable=True))

    with op.batch_alter_table("notifications") as batch_op:
        batch_op.add_column(sa.Column("lead_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("scheduled_for", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_notifications_lead_id", ["lead_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_notifications_lead_id_leads",
            "leads",
            ["lead_id"],
            ["id"],
            ondelete="CASCADE",
        )

    op.create_table(
        "course_lessons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_id", "sequence", name="uq_course_lesson_sequence"),
    )
    op.create_index("ix_course_lessons_id", "course_lessons", ["id"])
    op.create_index("ix_course_lessons_course_id", "course_lessons", ["course_id"])


def downgrade() -> None:
    op.drop_index("ix_course_lessons_course_id", table_name="course_lessons")
    op.drop_index("ix_course_lessons_id", table_name="course_lessons")
    op.drop_table("course_lessons")

    with op.batch_alter_table("notifications") as batch_op:
        batch_op.drop_constraint("fk_notifications_lead_id_leads", type_="foreignkey")
        batch_op.drop_index("ix_notifications_lead_id")
        batch_op.drop_column("scheduled_for")
        batch_op.drop_column("lead_id")

    with op.batch_alter_table("lead_activities") as batch_op:
        batch_op.drop_column("scheduled_for")

    with op.batch_alter_table("leads") as batch_op:
        batch_op.drop_constraint(
            "fk_leads_assigned_to_user_id_users", type_="foreignkey"
        )
        batch_op.drop_index("ix_leads_assigned_to_user_id")
        batch_op.drop_column("assigned_to_user_id")
