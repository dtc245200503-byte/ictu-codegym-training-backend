"""add training management domain

Revision ID: 4d7a2e1c9b31
Revises: eb9d4f8a2dbb
Create Date: 2026-10-11 06:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4d7a2e1c9b31"
down_revision: Union[str, Sequence[str], None] = "eb9d4f8a2dbb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("student_code", sa.String(50), nullable=True))
    op.add_column("users", sa.Column("phone", sa.String(30), nullable=True))
    op.add_column("users", sa.Column("birth_date", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("address", sa.String(255), nullable=True))
    op.create_index("ix_users_student_code", "users", ["student_code"], unique=True)

    op.create_table(
        "courses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("department", sa.String(100), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("prerequisite_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.ForeignKeyConstraint(
            ["prerequisite_id"], ["courses.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_courses_id", "courses", ["id"])
    op.create_index("ix_courses_code", "courses", ["code"], unique=True)
    op.create_index("ix_courses_name", "courses", ["name"])
    op.create_index("ix_courses_department", "courses", ["department"])

    op.create_table(
        "training_classes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("teacher_id", sa.Integer(), nullable=False),
        sa.Column("assistant_id", sa.Integer(), nullable=True),
        sa.Column("room", sa.String(80), nullable=False),
        sa.Column("term", sa.String(50), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("capacity", sa.Integer(), server_default=sa.text("40"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "day_of_week BETWEEN 0 AND 6", name="ck_class_weekday"
        ),
        sa.CheckConstraint("start_time < end_time", name="ck_class_time_range"),
        sa.ForeignKeyConstraint(
            ["course_id"], ["courses.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["teacher_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assistant_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_training_classes_id", "training_classes", ["id"])
    op.create_index(
        "ix_training_classes_code", "training_classes", ["code"], unique=True
    )

    op.create_table(
        "class_enrollments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column(
            "enrolled_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["class_id"], ["training_classes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("class_id", "student_id", name="uq_class_student"),
    )
    op.create_index("ix_class_enrollments_id", "class_enrollments", ["id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("target_user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["target_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_id", "audit_logs", ["id"])

    roles = sa.table(
        "roles",
        sa.column("id", sa.Integer()),
        sa.column("code", sa.String(50)),
        sa.column("name", sa.String(100)),
        sa.column("description", sa.Text()),
    )
    op.bulk_insert(
        roles,
        [
            {"id": 1, "code": "admin", "name": "Admin", "description": "Quản trị toàn hệ thống"},
            {"id": 2, "code": "manager", "name": "Training Manager", "description": "Quản lý nghiệp vụ đào tạo"},
            {"id": 3, "code": "admissions", "name": "Admissions", "description": "Tuyển sinh và ghi danh học viên"},
            {"id": 4, "code": "accountant", "name": "Accountant", "description": "Theo dõi học phí"},
            {"id": 5, "code": "teacher", "name": "Instructor", "description": "Quản lý lớp được phân công"},
            {"id": 6, "code": "ta", "name": "TA", "description": "Trợ giảng và điểm danh lớp được phân công"},
            {"id": 7, "code": "student", "name": "Student", "description": "Xem lớp, lịch học và bài tập cá nhân"},
            {"id": 8, "code": "guest", "name": "Guest", "description": "Tài khoản khách với quyền truy cập giới hạn"},
        ],
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_index("ix_class_enrollments_id", table_name="class_enrollments")
    op.drop_table("class_enrollments")
    op.drop_index("ix_training_classes_code", table_name="training_classes")
    op.drop_index("ix_training_classes_id", table_name="training_classes")
    op.drop_table("training_classes")
    op.drop_index("ix_courses_department", table_name="courses")
    op.drop_index("ix_courses_name", table_name="courses")
    op.drop_index("ix_courses_code", table_name="courses")
    op.drop_index("ix_courses_id", table_name="courses")
    op.drop_table("courses")
    op.drop_index("ix_users_student_code", table_name="users")
    op.drop_column("users", "address")
    op.drop_column("users", "birth_date")
    op.drop_column("users", "phone")
    op.drop_column("users", "student_code")
