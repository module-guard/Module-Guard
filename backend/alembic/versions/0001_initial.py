"""Module, scan and normalized finding storage.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

JSON_DATA = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    op.create_table(
        "modules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "source_type",
            sa.Enum(
                "GITHUB",
                "TERRAFORM_REGISTRY",
                name="sourcetype",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("repository_owner", sa.String(255)),
        sa.Column("repository_name", sa.String(255)),
        sa.Column("default_branch", sa.String(255)),
        sa.Column("provider", sa.String(100)),
        sa.Column("latest_version", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_type", "source_url", name="uq_module_source"),
    )
    op.create_table(
        "scans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "module_id", sa.Uuid(), sa.ForeignKey("modules.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "status",
            sa.Enum(
                "QUEUED",
                "COLLECTING",
                "SCANNING",
                "NORMALIZING",
                "COMPLETED",
                "FAILED",
                name="scanstatus",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("scanner", sa.String(64), nullable=False),
        sa.Column("commit_hash", sa.String(64)),
        sa.Column("branch", sa.String(255)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        *[
            sa.Column(name, sa.Integer(), nullable=False)
            for name in (
                "total_findings",
                "critical_count",
                "high_count",
                "medium_count",
                "low_count",
                "info_count",
                "unknown_count",
            )
        ],
        sa.Column("partial_result", sa.Boolean(), nullable=False),
        sa.Column("warnings", JSON_DATA, nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("raw_result", JSON_DATA),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "findings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "scan_id", sa.Uuid(), sa.ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("rule_id", sa.String(255), nullable=False),
        sa.Column("scanner", sa.String(64), nullable=False),
        sa.Column(
            "severity",
            sa.Enum(
                "CRITICAL",
                "HIGH",
                "MEDIUM",
                "LOW",
                "INFO",
                "UNKNOWN",
                name="severity",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text()),
        sa.Column("resource_type", sa.String(255)),
        sa.Column("file_path", sa.Text()),
        sa.Column("line_start", sa.Integer()),
        sa.Column("line_end", sa.Integer()),
        sa.Column("description", sa.Text()),
        sa.Column("guideline", sa.Text()),
        sa.Column("check_result", sa.String(32), nullable=False),
        sa.Column("raw_data", JSON_DATA, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for table, columns in {
        "modules": ["created_at"],
        "scans": ["module_id", "status", "created_at"],
        "findings": ["scan_id", "severity", "rule_id", "resource_type", "created_at"],
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])
    op.create_index("ix_findings_scan_severity", "findings", ["scan_id", "severity"])


def downgrade():
    op.drop_table("findings")
    op.drop_table("scans")
    op.drop_table("modules")
