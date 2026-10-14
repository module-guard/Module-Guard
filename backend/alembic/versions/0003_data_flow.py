"""Allow honest unresolved sources in the shared graph (no synthetic entity owners).

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("graph_edges") as batch:
        batch.alter_column("source_entity_id", existing_type=sa.Uuid(), nullable=True)


def downgrade():
    # Remove phase-3 artifacts only; every phase-2 dependency edge and entity is preserved.
    op.execute(
        sa.text(
            "DELETE FROM graph_edges WHERE edge_type IN "
            "('FLOWS_TO', 'DERIVED_FROM', 'PASSES_TO_MODULE_INPUT', "
            "'RETURNS_FROM_MODULE_OUTPUT', 'PROPAGATES_THROUGH_LOCAL', "
            "'SINKS_TO_RESOURCE_ATTRIBUTE', 'PASSES_TO_OUTPUT')"
        )
    )
    with op.batch_alter_table("graph_edges") as batch:
        batch.alter_column("source_entity_id", existing_type=sa.Uuid(), nullable=False)
    # Analysis is historical stage output; flow APIs always count persisted edges.
