"""Immutable version manifests and final download artifacts. No legacy hash backfill.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa

from alembic import op
from app.models.common import JSON_DATA

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        sa.Column("manifest", JSON_DATA, nullable=True),
        sa.Column("manifest_sha256", sa.String(64), nullable=True),
        sa.Column("integrity_status", sa.String(16), nullable=False, server_default="UNVERIFIED"),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("module_path", sa.String(2048), nullable=False, server_default="."),
        sa.Column("mismatched_files", JSON_DATA, nullable=True),
    ):
        op.add_column("module_versions", column)
    op.create_table(
        "download_artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "version_id",
            sa.Uuid(),
            sa.ForeignKey("module_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("zip_sha256", sa.String(64), nullable=False),
        sa.Column("artifact_size", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("integrity_status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("version_id", "request_sha256", name="uq_artifact_request"),
    )
    op.create_index("ix_download_artifacts_version_id", "download_artifacts", ["version_id"])


def downgrade():
    op.drop_table("download_artifacts")
    with op.batch_alter_table("module_versions") as batch:
        for name in (
            "manifest",
            "manifest_sha256",
            "integrity_status",
            "verified_at",
            "collected_at",
            "source_url",
            "module_path",
            "mismatched_files",
        ):
            batch.drop_column(name)
