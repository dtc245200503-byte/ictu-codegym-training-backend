"""Use explicit subject and order columns for program curricula."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6f2a91c84d30"
down_revision: Union[str, Sequence[str], None] = "c4e8a910d672"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "program_subjects",
        sa.Column("program_id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column(
            "order_index",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("prerequisite_subject_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["program_id"],
            ["study_programs.id"],
            name="fk_program_subjects_program_id_study_programs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["courses.id"],
            name="fk_program_subjects_subject_id_courses",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prerequisite_subject_id"],
            ["courses.id"],
            name="fk_program_subjects_prerequisite_subject_id_courses",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "program_id", "subject_id", name="pk_program_subjects"
        ),
    )
    op.execute(
        """
        INSERT INTO program_subjects
            (program_id, subject_id, order_index, prerequisite_subject_id)
        SELECT program_id, course_id, sequence, prerequisite_course_id
        FROM program_courses
        """
    )
    op.drop_table("program_courses")


def downgrade() -> None:
    op.create_table(
        "program_courses",
        sa.Column("program_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("prerequisite_course_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["program_id"],
            ["study_programs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["prerequisite_course_id"], ["courses.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("program_id", "course_id"),
    )
    op.execute(
        """
        INSERT INTO program_courses
            (program_id, course_id, sequence, prerequisite_course_id)
        SELECT program_id, subject_id, order_index, prerequisite_subject_id
        FROM program_subjects
        """
    )
    op.drop_table("program_subjects")
