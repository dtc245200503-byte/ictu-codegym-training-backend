"""Add Sprint 3 admissions, student profiles, and classroom catalog."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4b6c2d9a130"
down_revision: Union[str, Sequence[str], None] = "d3a7f2c19b64"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch_op:
        batch_op.add_column(sa.Column("converted_user_id", sa.Integer(), nullable=True))
        batch_op.create_unique_constraint(
            "uq_leads_converted_user_id", ["converted_user_id"]
        )
        batch_op.create_foreign_key(
            "fk_leads_converted_user_id_users",
            "users",
            ["converted_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_table(
        "lead_activities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lead_activities_id", "lead_activities", ["id"])
    op.create_index("ix_lead_activities_lead_id", "lead_activities", ["lead_id"])
    op.create_table(
        "student_profiles",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("study_program", sa.String(length=150), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("lead_id", name="uq_student_profiles_lead_id"),
    )
    op.create_table(
        "classrooms",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("online_url", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_classrooms_code"),
    )
    op.create_index("ix_classrooms_id", "classrooms", ["id"])
    op.create_index("ix_classrooms_code", "classrooms", ["code"])
    op.execute(
        """
        INSERT INTO classrooms (code, capacity)
        SELECT room, MAX(capacity)
        FROM training_classes
        GROUP BY room
        """
    )


def downgrade() -> None:
    op.drop_index("ix_classrooms_code", table_name="classrooms")
    op.drop_index("ix_classrooms_id", table_name="classrooms")
    op.drop_table("classrooms")
    op.drop_table("student_profiles")
    op.drop_index("ix_lead_activities_lead_id", table_name="lead_activities")
    op.drop_index("ix_lead_activities_id", table_name="lead_activities")
    op.drop_table("lead_activities")
    with op.batch_alter_table("leads") as batch_op:
        batch_op.drop_constraint(
            "fk_leads_converted_user_id_users", type_="foreignkey"
        )
        batch_op.drop_constraint("uq_leads_converted_user_id", type_="unique")
        batch_op.drop_column("converted_user_id")
