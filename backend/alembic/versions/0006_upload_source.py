"""Allow modules whose source is an uploaded archive.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

OLD = ("GITHUB", "TERRAFORM_REGISTRY")
NEW = (*OLD, "UPLOAD")


def _enum(values):
    return sa.Enum(*values, name="sourcetype", native_enum=False, create_constraint=True)


def _retype(old, new):
    # For a non-native Enum, Alembic swaps the CHECK constraint together with the type.
    with op.batch_alter_table("modules") as batch:
        batch.alter_column(
            "source_type",
            existing_type=_enum(old),
            type_=_enum(new),
            existing_nullable=False,
        )


def upgrade():
    _retype(OLD, NEW)


def downgrade():
    # Uploaded modules cannot exist in the previous schema; their scans cascade with them.
    op.execute(sa.text("DELETE FROM modules WHERE source_type = 'UPLOAD'"))
    _retype(NEW, OLD)
