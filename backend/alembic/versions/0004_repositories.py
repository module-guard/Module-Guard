"""Users, repositories, immutable module versions and stars on top of the scan pipeline.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None
JSON_DATA = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("github_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("login", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(255)),
        sa.Column("avatar_url", sa.String(2048)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "repositories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("provider", sa.String(100)),
        sa.Column(
            "visibility",
            sa.Enum(
                "PUBLIC", "PRIVATE", name="visibility", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column(
            "forked_from_id", sa.Uuid(), sa.ForeignKey("repositories.id", ondelete="SET NULL")
        ),
        sa.Column(
            "origin_type",
            sa.Enum(
                "GITHUB",
                "UPLOAD",
                "FORK",
                name="origintype",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("origin_url", sa.String(2048)),
        sa.Column("star_count", sa.Integer(), nullable=False),
        sa.Column("fork_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "name", name="uq_repository_owner_name"),
    )
    for column in ("owner_id", "visibility", "forked_from_id", "created_at"):
        op.create_index(f"ix_repositories_{column}", "repositories", [column])
    op.create_table(
        "module_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repository_id", sa.Uuid(), sa.ForeignKey("repositories.id", ondelete="CASCADE")),
        sa.Column("version", sa.String(100)),
        sa.Column("snapshot_sha256", sa.String(64)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("file_count", sa.Integer()),
        sa.Column("commit_hash", sa.String(64)),
        sa.Column("readme", sa.Text()),
        sa.Column("variables", JSON_DATA),
        sa.Column("outputs", JSON_DATA),
        sa.Column("license", sa.String(64)),
        sa.Column("scan_id", sa.Uuid(), sa.ForeignKey("scans.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repository_id", "version", name="uq_version_repository"),
    )
    for column in ("repository_id", "snapshot_sha256", "scan_id", "created_at"):
        op.create_index(f"ix_module_versions_{column}", "module_versions", [column])
    op.create_table(
        "stars",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "repository_id",
            sa.Uuid(),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_stars_repository_id", "stars", ["repository_id"])
    with op.batch_alter_table("scans") as batch:
        batch.add_column(sa.Column("module_version_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_scan_module_version",
            "module_versions",
            ["module_version_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_scans_module_version_id", ["module_version_id"])


def downgrade():
    # Removes only the repository layer; modules, scans, findings and graphs are preserved.
    with op.batch_alter_table("scans") as batch:
        batch.drop_index("ix_scans_module_version_id")
        batch.drop_constraint("fk_scan_module_version", type_="foreignkey")
        batch.drop_column("module_version_id")
    op.drop_table("stars")
    op.drop_table("module_versions")
    op.drop_table("repositories")
    op.drop_table("users")
