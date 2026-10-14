"""Separate findings of the module body from those of its examples and tests.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None
JSON_DATA = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    # Existing findings start as MODULE; scripts/reapply_severity.py classifies them.
    with op.batch_alter_table("findings") as batch:
        batch.add_column(sa.Column("scope", sa.String(16), nullable=False, server_default="MODULE"))
        batch.create_index("ix_findings_scope", ["scope"])
    op.add_column("scans", sa.Column("scope_summary", JSON_DATA, nullable=True))


def downgrade():
    op.drop_column("scans", "scope_summary")
    with op.batch_alter_table("findings") as batch:
        batch.drop_index("ix_findings_scope")
        batch.drop_column("scope")
