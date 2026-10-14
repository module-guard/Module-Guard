"""System accounts (no GitHub identity, cannot sign in) and the fetched ref of a version.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.alter_column("github_id", existing_type=sa.BigInteger(), nullable=True)
    with op.batch_alter_table("module_versions") as batch:
        batch.add_column(sa.Column("source_ref", sa.String(100), nullable=True))


def downgrade():
    with op.batch_alter_table("module_versions") as batch:
        batch.drop_column("source_ref")
    # github_id becomes required again: system accounts keep their rows under ids GitHub
    # never issues (negative), so their repositories are not lost.
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id FROM users WHERE github_id IS NULL")).all()
    for number, (user_id,) in enumerate(rows, start=1):
        connection.execute(
            sa.text("UPDATE users SET github_id = :github_id WHERE id = :id"),
            {"github_id": -number, "id": user_id},
        )
    with op.batch_alter_table("users") as batch:
        batch.alter_column("github_id", existing_type=sa.BigInteger(), nullable=False)
        batch.drop_column("is_system")
