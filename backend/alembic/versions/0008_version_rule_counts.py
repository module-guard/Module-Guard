"""Cache the badge numbers (distinct CRITICAL / HIGH rules) on each version.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    # Existing versions start at 0; scripts/reapply_severity.py fills them in.
    with op.batch_alter_table("module_versions") as batch:
        for name in ("critical_rules", "high_rules"):
            batch.add_column(sa.Column(name, sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    with op.batch_alter_table("module_versions") as batch:
        batch.drop_column("high_rules")
        batch.drop_column("critical_rules")
