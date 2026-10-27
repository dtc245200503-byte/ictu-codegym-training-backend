"""Record the staff member who enrolled a student in a class."""
from alembic import op
import sqlalchemy as sa


revision = "o5f9d3a1c842"
down_revision = "n4e8c2b7d103f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("class_enrollments") as batch_op:
        batch_op.add_column(sa.Column("enrolled_by_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_class_enrollments_enrolled_by_id", ["enrolled_by_id"])
        batch_op.create_foreign_key(
            "fk_class_enrollments_enrolled_by_id_users", "users", ["enrolled_by_id"], ["id"], ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("class_enrollments") as batch_op:
        batch_op.drop_constraint("fk_class_enrollments_enrolled_by_id_users", type_="foreignkey")
        batch_op.drop_index("ix_class_enrollments_enrolled_by_id")
        batch_op.drop_column("enrolled_by_id")
