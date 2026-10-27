"""Allow private files to be attached to assignment instructions."""
from alembic import op
import sqlalchemy as sa


revision = "h61a4c9e2b30"
down_revision = "g31a9e5c2d70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("private_assignment_files", recreate="always") as batch:
        batch.drop_constraint("ck_private_file_parent", type_="check")
        batch.add_column(sa.Column("assignment_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_private_assignment_files_assignment_id_assignments",
            "assignments",
            ["assignment_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_private_assignment_files_assignment_id", ["assignment_id"])
        batch.create_check_constraint(
            "ck_private_file_parent",
            "(assignment_id IS NOT NULL AND submission_id IS NULL AND session_id IS NULL) OR "
            "(assignment_id IS NULL AND submission_id IS NOT NULL AND session_id IS NULL) OR "
            "(assignment_id IS NULL AND submission_id IS NULL AND session_id IS NOT NULL)",
        )


def downgrade() -> None:
    connection = op.get_bind()
    assignment_files = connection.scalar(
        sa.text("SELECT COUNT(*) FROM private_assignment_files WHERE assignment_id IS NOT NULL")
    )
    if assignment_files:
        raise RuntimeError(
            "Cannot downgrade assignment instruction files while private files still reference assignments."
        )
    with op.batch_alter_table("private_assignment_files", recreate="always") as batch:
        batch.drop_constraint("ck_private_file_parent", type_="check")
        batch.drop_index("ix_private_assignment_files_assignment_id")
        batch.drop_constraint(
            "fk_private_assignment_files_assignment_id_assignments", type_="foreignkey"
        )
        batch.drop_column("assignment_id")
        batch.create_check_constraint(
            "ck_private_file_parent",
            "(submission_id IS NOT NULL AND session_id IS NULL) OR "
            "(submission_id IS NULL AND session_id IS NOT NULL)",
        )
