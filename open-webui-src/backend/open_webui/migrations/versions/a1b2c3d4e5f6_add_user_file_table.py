"""Add user_file grant table (MINTIFY content dedup)

Revision ID: a1b2c3d4e5f6
Revises: 3781e22d8b01
Create Date: 2026-06-09 00:00:00.000000

The `file` table becomes a pure content store (one row per unique document, deduped
by raw-bytes hash). This junction table records which user can see which file, so the
same physical file is shared across accounts without duplicating disk bytes or vectors.
"""

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "3781e22d8b01"
branch_labels = None
depends_on = None


def upgrade():
    print("Creating user_file table")
    op.create_table(
        "user_file",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("file_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=True),
    )


def downgrade():
    op.drop_table("user_file")
