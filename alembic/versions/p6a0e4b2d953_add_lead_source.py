"""Track lead acquisition source for CRM filtering and reporting."""
from alembic import op
import sqlalchemy as sa


revision = "p6a0e4b2d953"
down_revision = "o5f9d3a1c842"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("source", sa.String(length=80), server_default="website", nullable=False))
    op.create_index("ix_leads_source", "leads", ["source"])


def downgrade() -> None:
    op.drop_index("ix_leads_source", table_name="leads")
    op.drop_column("leads", "source")
