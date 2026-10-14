"""Persist semantic entities, dependency edges and additive analysis status.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None
JSON_DATA = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    op.add_column("scans", sa.Column("analysis", JSON_DATA, nullable=True))
    op.create_table(
        "parsed_entities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "scan_id", sa.Uuid(), sa.ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.String(255)),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("line_start", sa.Integer()),
        sa.Column("line_end", sa.Integer()),
        sa.Column("attributes", JSON_DATA, nullable=False),
        sa.Column("metadata", JSON_DATA, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scan_id", "address", name="uq_entity_scan_address"),
        sa.UniqueConstraint("scan_id", "id", name="uq_entity_scan_id"),
    )
    op.create_table(
        "graph_edges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "scan_id", sa.Uuid(), sa.ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("source_entity_id", sa.Uuid(), nullable=False),
        sa.Column("target_entity_id", sa.Uuid()),
        sa.Column("target_address", sa.Text()),
        sa.Column("edge_type", sa.String(80), nullable=False),
        sa.Column("expression", sa.Text()),
        sa.Column("attribute_name", sa.Text()),
        sa.Column("metadata", JSON_DATA, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["scan_id", "source_entity_id"],
            ["parsed_entities.scan_id", "parsed_entities.id"],
            ondelete="CASCADE",
            name="fk_graph_source_scan",
        ),
        sa.ForeignKeyConstraint(
            ["scan_id", "target_entity_id"],
            ["parsed_entities.scan_id", "parsed_entities.id"],
            ondelete="CASCADE",
            name="fk_graph_target_scan",
        ),
    )
    for table, columns in {
        "parsed_entities": ["scan_id", "entity_type", "address", "resource_type"],
        "graph_edges": ["scan_id", "source_entity_id", "target_entity_id", "edge_type"],
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    op.drop_table("graph_edges")
    op.drop_table("parsed_entities")
    op.drop_column("scans", "analysis")
