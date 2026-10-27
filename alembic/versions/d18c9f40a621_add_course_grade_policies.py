"""Add versioned course grade policies and final grade snapshots."""
from alembic import op
import sqlalchemy as sa


revision = "d18c9f40a621"
down_revision = "c4e71a9d2b60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assignments", sa.Column("grade_component_key", sa.String(length=50), nullable=True))
    op.create_index("ix_assignments_grade_component_key", "assignments", ["grade_component_key"])
    op.create_table(
        "grade_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("program_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=True),
        sa.Column("scope_key", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("components", sa.JSON(), nullable=False),
        sa.Column("pass_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("critical_component_key", sa.String(length=50), nullable=True),
        sa.Column("critical_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.CheckConstraint("pass_score BETWEEN 0 AND 100", name="ck_grade_policy_pass_score"),
        sa.CheckConstraint("critical_score IS NULL OR critical_score BETWEEN 0 AND 100", name="ck_grade_policy_critical_score"),
        sa.ForeignKeyConstraint(["program_id"], ["study_programs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["class_id"], ["training_classes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("program_id", "course_id", "scope_key", "version", name="uq_grade_policy_scope_version"),
    )
    op.create_index("ix_grade_policies_program_id", "grade_policies", ["program_id"])
    op.create_index("ix_grade_policies_course_id", "grade_policies", ["course_id"])
    op.create_index("ix_grade_policies_class_id", "grade_policies", ["class_id"])
    op.create_table(
        "course_final_grades",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(5, 2), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("breakdown", sa.JSON(), nullable=False),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("finalized_by", sa.Integer(), nullable=True),
        sa.Column("finalized_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("reopened_by", sa.Integer(), nullable=True),
        sa.Column("reopened_at", sa.DateTime(), nullable=True),
        sa.Column("reopen_reason", sa.String(length=1000), nullable=True),
        sa.CheckConstraint("result IN ('incomplete', 'pass', 'fail')", name="ck_course_final_grade_result"),
        sa.CheckConstraint("score BETWEEN 0 AND 100", name="ck_course_final_grade_score"),
        sa.ForeignKeyConstraint(["class_id"], ["training_classes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["policy_id"], ["grade_policies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["finalized_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reopened_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("class_id", "student_id", "course_id", "version", name="uq_course_final_grade_version"),
    )
    op.create_index("ix_course_final_grades_class_id", "course_final_grades", ["class_id"])
    op.create_index("ix_course_final_grades_student_id", "course_final_grades", ["student_id"])
    op.create_index("ix_course_final_grades_course_id", "course_final_grades", ["course_id"])


def downgrade() -> None:
    op.drop_index("ix_course_final_grades_course_id", table_name="course_final_grades")
    op.drop_index("ix_course_final_grades_student_id", table_name="course_final_grades")
    op.drop_index("ix_course_final_grades_class_id", table_name="course_final_grades")
    op.drop_table("course_final_grades")
    op.drop_index("ix_grade_policies_class_id", table_name="grade_policies")
    op.drop_index("ix_grade_policies_course_id", table_name="grade_policies")
    op.drop_index("ix_grade_policies_program_id", table_name="grade_policies")
    op.drop_table("grade_policies")
    op.drop_index("ix_assignments_grade_component_key", table_name="assignments")
    op.drop_column("assignments", "grade_component_key")
