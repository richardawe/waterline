"""collections: WhatsApp Cloud API (templates, sent messages, queue values)

- cl_wa_template: each of our message templates as submitted to Meta, with
  its review status. Named after segment/key/version so an edited template
  is a new Meta template.
- cl_wa_message: every WhatsApp message the portal sends, with Meta's
  message id (wamid) so delivery/read/failed webhooks can update it.
- cl_outreach_item.render_values: the values a queued message was built
  from (encrypted JSON); Meta templates need them as parameters.

Revision ID: c5f2a9e1d6b3
Revises: b3e8d1c4f7a2
Create Date: 2026-11-02 14:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c5f2a9e1d6b3'
down_revision: Union[str, None] = 'b3e8d1c4f7a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cl_wa_template",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("segment", sa.String(20), nullable=False),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("variant", sa.String(10), nullable=False, server_default=""),
        sa.Column("meta_name", sa.String(120), nullable=False, unique=True),
        sa.Column("meta_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="NOT_SUBMITTED"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "cl_wa_message",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("wamid", sa.String(200), nullable=True, unique=True),
        sa.Column("to_phone", sa.Text(), nullable=False),
        sa.Column("to_index", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=True),
        sa.Column("prospect_id", sa.String(64), nullable=True),
        sa.Column("client_id", sa.String(64), nullable=True),
        sa.Column("outreach_item_id", sa.String(64), nullable=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("template_name", sa.String(120), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_by", sa.String(64), nullable=True),
        sa.Column("status_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_cl_wa_message_to_index", "cl_wa_message", ["to_index"])
    op.create_index("ix_cl_wa_message_account_id", "cl_wa_message", ["account_id"])
    op.create_index("ix_cl_wa_message_prospect_id", "cl_wa_message", ["prospect_id"])
    op.create_index("ix_cl_wa_message_outreach_item_id", "cl_wa_message", ["outreach_item_id"])
    op.add_column("cl_outreach_item", sa.Column("render_values", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("cl_outreach_item", "render_values")
    op.drop_table("cl_wa_message")
    op.drop_table("cl_wa_template")
