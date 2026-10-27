"""Persist explicit program ownership for classes and finance reporting."""
from alembic import op
import sqlalchemy as sa


revision = "d4c9e1a73b20"
down_revision = "c3b7e1290a64"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("training_classes") as batch:
        batch.add_column(sa.Column("program_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_training_classes_program_id", "study_programs",
            ["program_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_training_classes_program_id", "training_classes", ["program_id"])


def downgrade() -> None:
    op.drop_index("ix_training_classes_program_id", table_name="training_classes")
    with op.batch_alter_table("training_classes") as batch:
        batch.drop_constraint("fk_training_classes_program_id", type_="foreignkey")
        batch.drop_column("program_id")
