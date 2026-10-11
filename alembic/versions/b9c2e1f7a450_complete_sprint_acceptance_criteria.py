"""Complete sprint acceptance criteria for leads, syllabus, and classes."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b9c2e1f7a450"
down_revision: Union[str, Sequence[str], None] = "a71c4e902d5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return column_name in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return index_name in {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)
    }


def _has_foreign_key(table_name: str, constraint_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return constraint_name in {
        constraint["name"]
        for constraint in sa.inspect(op.get_bind()).get_foreign_keys(table_name)
    }


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("leads") as batch_op:
            batch_op.alter_column(
                "status",
                existing_type=sa.String(length=20),
                type_=sa.String(length=24),
                existing_nullable=False,
            )
    else:
        op.alter_column(
            "leads",
            "status",
            existing_type=sa.String(length=20),
            type_=sa.String(length=24),
            existing_nullable=False,
        )
    if not _has_column("leads", "rejection_reason"):
        op.add_column("leads", sa.Column("rejection_reason", sa.Text(), nullable=True))
    op.execute("UPDATE leads SET status = 'closed' WHERE status = 'qualified'")
    if not _has_column("lead_activities", "activity_type"):
        op.add_column(
            "lead_activities",
            sa.Column(
                "activity_type",
                sa.String(length=20),
                server_default="call",
                nullable=False,
            ),
        )

    if not _has_column("courses", "session_count"):
        op.add_column(
            "courses",
            sa.Column(
                "session_count", sa.Integer(), server_default="16", nullable=False
            ),
        )
    if not _has_column("course_lessons", "objectives"):
        op.add_column(
            "course_lessons", sa.Column("objectives", sa.Text(), nullable=True)
        )
    op.execute("UPDATE course_lessons SET objectives = '' WHERE objectives IS NULL")
    with op.batch_alter_table("course_lessons") as batch_op:
        batch_op.alter_column(
            "objectives",
            existing_type=sa.Text(),
            nullable=False,
        )

    if not _has_column("program_courses", "sequence"):
        op.add_column(
            "program_courses",
            sa.Column("sequence", sa.Integer(), server_default="0", nullable=False),
        )
    if not _has_column("program_courses", "prerequisite_course_id"):
        op.add_column(
            "program_courses",
            sa.Column("prerequisite_course_id", sa.Integer(), nullable=True),
        )
    prerequisite_fk = "fk_program_courses_prerequisite_course_id_courses"
    if not _has_foreign_key("program_courses", prerequisite_fk):
        with op.batch_alter_table("program_courses") as batch_op:
            batch_op.create_foreign_key(
                prerequisite_fk,
                "courses",
                ["prerequisite_course_id"],
                ["id"],
                ondelete="SET NULL",
            )
    op.execute("UPDATE program_courses SET sequence = course_id WHERE sequence = 0")

    if not _has_column("training_classes", "status"):
        op.add_column(
            "training_classes",
            sa.Column(
                "status",
                sa.String(length=20),
                server_default="preparing",
                nullable=False,
            ),
        )
    if not _has_table("class_assistants"):
        op.create_table(
            "class_assistants",
            sa.Column("class_id", sa.Integer(), nullable=False),
            sa.Column("assistant_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(
                ["class_id"], ["training_classes.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["assistant_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("class_id", "assistant_id"),
        )
    if not _has_index("class_assistants", "ix_class_assistants_assistant_id"):
        op.create_index(
            "ix_class_assistants_assistant_id",
            "class_assistants",
            ["assistant_id"],
            unique=False,
        )
    op.execute(
        "INSERT INTO class_assistants (class_id, assistant_id) "
        "SELECT tc.id, tc.assistant_id FROM training_classes tc "
        "WHERE tc.assistant_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM class_assistants ca "
        "WHERE ca.class_id = tc.id AND ca.assistant_id = tc.assistant_id)"
    )


def downgrade() -> None:
    op.drop_index("ix_class_assistants_assistant_id", table_name="class_assistants")
    op.drop_table("class_assistants")
    op.drop_column("training_classes", "status")

    with op.batch_alter_table("program_courses") as batch_op:
        batch_op.drop_constraint(
            "fk_program_courses_prerequisite_course_id_courses",
            type_="foreignkey",
        )
    op.drop_column("program_courses", "prerequisite_course_id")
    op.drop_column("program_courses", "sequence")

    op.drop_column("course_lessons", "objectives")
    op.drop_column("courses", "session_count")

    op.drop_column("lead_activities", "activity_type")
    op.drop_column("leads", "rejection_reason")
    op.execute("UPDATE leads SET status = 'qualified' WHERE status = 'closed'")
    op.alter_column(
        "leads",
        "status",
        existing_type=sa.String(length=24),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
