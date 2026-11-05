"""Append-only inspection reuse decisions.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa

from alembic import op
from app.models.common import JSON_DATA

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "scan_reuse",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "target_scan_id",
            sa.Uuid(),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("source_scan_id", sa.Uuid(), sa.ForeignKey("scans.id", ondelete="SET NULL")),
        sa.Column(
            "source_version_id", sa.Uuid(), sa.ForeignKey("module_versions.id", ondelete="SET NULL")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_finished_at", sa.DateTime(timezone=True)),
        sa.Column("reused", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(80), nullable=False),
        sa.Column("key", sa.String(64)),
        sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("conditions", JSON_DATA, nullable=False),
        sa.Column("verification", JSON_DATA, nullable=False),
        sa.Column("source_duration_ms", sa.Integer()),
        sa.Column("lookup_duration_ms", sa.Integer(), nullable=False),
    )
    op.create_index("ix_scan_reuse_source_scan_id", "scan_reuse", ["source_scan_id"])


def downgrade():
    op.drop_table("scan_reuse")
