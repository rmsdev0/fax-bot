"""Label built-in fictional gallery samples.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-12
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gallery_items",
        sa.Column("is_sample", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("gallery_items", "is_sample")
