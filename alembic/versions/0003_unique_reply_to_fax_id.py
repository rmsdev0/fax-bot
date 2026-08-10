"""Unique constraint on outbound_faxes.reply_to_fax_id.

One reply row per inbound fax, enforced by the database so a racing
duplicate insert in delivery.send_reply fails instead of double-sending.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-10
"""

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_outbound_faxes_reply_to_fax_id", "outbound_faxes", ["reply_to_fax_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_outbound_faxes_reply_to_fax_id", "outbound_faxes", type_="unique")
