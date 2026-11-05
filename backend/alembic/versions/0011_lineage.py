"""Immutable lineage records for module versions (#26), with a backfill of stored versions.

Revision ID: 0011
Revises: 0010

Backfill: every stored repository version gets a record, flagged backfilled=True, with what is
already known. Its manifest and manifest hash are copied as they are (NULL before 0010, which
the API reports as "not comparable"); nothing is recomputed from snapshots. The first version
of a fork points at the origin version whose scan it shares; other later versions point at the
previous stored version of the same repository; the rest are ORIGINAL. The publisher is the
repository owner, the declaration version is NULL (published before there was one).
"""

from uuid import uuid4

import sqlalchemy as sa

from alembic import op
from app.models.common import JSON_DATA

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lineage_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column(
            "parent_id",
            sa.Uuid(),
            sa.ForeignKey("lineage_records.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("relation", sa.String(16), nullable=False),
        sa.Column("manifest", JSON_DATA, nullable=True),
        sa.Column("manifest_sha256", sa.String(64), nullable=True),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("commit_hash", sa.String(64), nullable=True),
        sa.Column("module_path", sa.String(2048), nullable=False),
        sa.Column("publisher_id", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("declaration_version", sa.String(64), nullable=True),
        sa.Column("backfilled", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("version_id"),
    )
    op.create_index("ix_lineage_records_repository_id", "lineage_records", ["repository_id"])
    op.create_index("ix_lineage_records_parent_id", "lineage_records", ["parent_id"])
    _backfill(op.get_bind())


def _backfill(connection):
    versions = sa.table(
        "module_versions",
        sa.column("id", sa.Uuid()),
        sa.column("repository_id", sa.Uuid()),
        sa.column("snapshot_sha256", sa.String()),
        sa.column("scan_id", sa.Uuid()),
        sa.column("manifest", JSON_DATA),
        sa.column("manifest_sha256", sa.String()),
        sa.column("source_url", sa.String()),
        sa.column("commit_hash", sa.String()),
        sa.column("module_path", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    repositories = sa.table(
        "repositories",
        sa.column("id", sa.Uuid()),
        sa.column("owner_id", sa.Uuid()),
        sa.column("forked_from_id", sa.Uuid()),
        sa.column("origin_url", sa.String()),
    )
    rows = connection.execute(
        sa.select(
            versions,
            repositories.c.owner_id,
            repositories.c.forked_from_id,
            repositories.c.origin_url,
        )
        .join(repositories, repositories.c.id == versions.c.repository_id)
        .where(versions.c.snapshot_sha256.is_not(None))
        .order_by(versions.c.repository_id, versions.c.created_at, versions.c.id)
    ).all()
    if not rows:
        return
    record_of = {row.id: uuid4() for row in rows}
    # Origin versions by (repository, scan): a fork's first version shares its origin's scan.
    by_scan = {(row.repository_id, row.scan_id): row.id for row in rows if row.scan_id}
    records, previous = [], {}
    for row in rows:
        before = previous.get(row.repository_id)
        if before is not None:
            relation, parent = "VERSION", record_of[before]
        elif row.forked_from_id and (row.forked_from_id, row.scan_id) in by_scan:
            relation, parent = "FORK", record_of[by_scan[(row.forked_from_id, row.scan_id)]]
        else:
            relation, parent = "ORIGINAL", None
        previous[row.repository_id] = row.id
        records.append(
            {
                "id": record_of[row.id],
                "version_id": row.id,
                "repository_id": row.repository_id,
                "parent_id": parent,
                "relation": relation,
                "manifest": row.manifest,
                "manifest_sha256": row.manifest_sha256,
                "source_url": row.source_url or row.origin_url,
                "commit_hash": row.commit_hash,
                "module_path": row.module_path or ".",
                "publisher_id": row.owner_id,
                "published_at": row.created_at,
                "declaration_version": None,
                "backfilled": True,
            }
        )
    lineage = sa.table(
        "lineage_records",
        *(
            sa.column(name, kind)
            for name, kind in (
                ("id", sa.Uuid()),
                ("version_id", sa.Uuid()),
                ("repository_id", sa.Uuid()),
                ("parent_id", sa.Uuid()),
                ("relation", sa.String()),
                ("manifest", JSON_DATA),
                ("manifest_sha256", sa.String()),
                ("source_url", sa.String()),
                ("commit_hash", sa.String()),
                ("module_path", sa.String()),
                ("publisher_id", sa.Uuid()),
                ("published_at", sa.DateTime(timezone=True)),
                ("declaration_version", sa.String()),
                ("backfilled", sa.Boolean()),
            )
        ),
    )
    # Parents first: the self-referencing FK must already see the parent row.
    inserted, pending = set(), list(records)
    while pending:
        ready = [r for r in pending if r["parent_id"] is None or r["parent_id"] in inserted]
        if not ready:  # a cycle cannot come from the data above; refuse rather than guess
            raise RuntimeError("lineage backfill found an unresolved parent")
        connection.execute(sa.insert(lineage), ready)
        inserted.update(r["id"] for r in ready)
        pending = [r for r in pending if r["id"] not in inserted]


def downgrade():
    op.drop_index("ix_lineage_records_parent_id", table_name="lineage_records")
    op.drop_index("ix_lineage_records_repository_id", table_name="lineage_records")
    op.drop_table("lineage_records")
