"""Allow/block entries and their append-only audit events (#47).

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa

from alembic import op
from app.models.common import JSON_DATA

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "allowblock_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column("target_key", sa.String(255), nullable=False),
        sa.Column(
            "repository_id",
            sa.Uuid(),
            sa.ForeignKey("repositories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("effect", sa.String(8), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_urls", JSON_DATA, nullable=False),
        sa.Column("public_message", sa.String(500), nullable=True),
        sa.Column(
            "created_by_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_by_login", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoked_by_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("revoked_by_login", sa.String(64), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.Column("revoke_evidence_urls", JSON_DATA, nullable=True),
        sa.CheckConstraint(
            "target_kind IN ('repository', 'github_repository', 'github_owner')",
            name="ck_allowblock_target_kind",
        ),
        sa.CheckConstraint("effect IN ('ALLOW', 'BLOCK')", name="ck_allowblock_effect"),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at", name="ck_allowblock_expiry_order"
        ),
    )
    op.create_index("ix_allowblock_entries_target_key", "allowblock_entries", ["target_key"])
    op.create_index("ix_allowblock_entries_repository_id", "allowblock_entries", ["repository_id"])
    op.create_index(
        "uq_allowblock_active_target",
        "allowblock_entries",
        ["target_kind", "target_key", "effect"],
        unique=True,
        sqlite_where=sa.text("revoked_at IS NULL"),
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "allowblock_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("entry_id", sa.Uuid(), sa.ForeignKey("allowblock_entries.id"), nullable=False),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column(
            "actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("actor_login", sa.String(64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_urls", JSON_DATA, nullable=False),
        sa.Column("snapshot", JSON_DATA, nullable=False),
        sa.CheckConstraint(
            "action IN ('CREATE', 'REVOKE', 'EXPIRE')", name="ck_allowblock_event_action"
        ),
    )
    op.create_index("ix_allowblock_events_entry_id", "allowblock_events", ["entry_id"])


def downgrade():
    op.drop_table("allowblock_events")
    op.drop_table("allowblock_entries")
