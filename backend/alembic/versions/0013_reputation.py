"""Version-bound reputation evidence snapshots.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa

from alembic import op
from app.models.common import JSON_DATA

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reputation_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "version_id",
            sa.Uuid(),
            sa.ForeignKey("module_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket", sa.String(32), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("payload", JSON_DATA, nullable=False),
        sa.UniqueConstraint("version_id", "bucket", name="uq_reputation_version_bucket"),
    )
    op.create_index("ix_reputation_snapshots_version_id", "reputation_snapshots", ["version_id"])


def downgrade():
    op.drop_table("reputation_snapshots")
