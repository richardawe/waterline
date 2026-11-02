"""collections: email inbox (cl_inbound), channel settings (cl_channel),
reply threading on cl_email, and the corrected WhatsApp number

- cl_channel holds connection settings an Admin enters in ops → Channels
  (the mailbox login is encrypted by the app before it gets here).
- cl_inbound holds messages people send us (email now, WhatsApp later),
  matched to a debtor account, prospect or client where possible.
- cl_email gains message_id / account_id / prospect_id / to_index / sent_by
  so replies can be threaded back to whoever we wrote to.
- contact_whatsapp: the stored value moves from +234 809 410 4100 to the
  correct +234 903 410 4100 (only if it still holds the old number).

Revision ID: b3e8d1c4f7a2
Revises: a7c4e91b2d30
Create Date: 2026-11-02 12:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b3e8d1c4f7a2'
down_revision: Union[str, None] = 'a7c4e91b2d30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_NUMBER = '"+234 809 410 4100"'
NEW_NUMBER = '"+234 903 410 4100"'


def upgrade() -> None:
    op.create_table(
        "cl_channel",
        sa.Column("key", sa.String(20), primary_key=True),
        sa.Column("settings", sa.Text(), nullable=True),  # encrypted JSON
        sa.Column("state", sa.Text(), nullable=True),  # plain JSON: last check, last error, IMAP position
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "cl_inbound",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("from_address", sa.Text(), nullable=False),
        sa.Column("from_index", sa.String(64), nullable=False),
        sa.Column("from_name", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("attachments", sa.Text(), nullable=True),
        sa.Column("message_id", sa.String(255), nullable=True, unique=True),
        sa.Column("in_reply_to", sa.String(255), nullable=True),
        sa.Column("party_type", sa.String(10), nullable=True),
        sa.Column("party_id", sa.String(64), nullable=True),
        sa.Column("client_id", sa.String(64), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handled_by", sa.String(64), nullable=True),
        sa.Column("auto_action", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_cl_inbound_from_index", "cl_inbound", ["from_index"])
    op.create_index("ix_cl_inbound_party", "cl_inbound", ["party_type", "party_id"])
    op.create_index("ix_cl_inbound_handled_at", "cl_inbound", ["handled_at"])

    op.add_column("cl_email", sa.Column("message_id", sa.String(255), nullable=True))
    op.add_column("cl_email", sa.Column("to_index", sa.String(64), nullable=True))
    op.add_column("cl_email", sa.Column("account_id", sa.String(64), nullable=True))
    op.add_column("cl_email", sa.Column("prospect_id", sa.String(64), nullable=True))
    op.add_column("cl_email", sa.Column("sent_by", sa.String(64), nullable=True))
    op.create_index("ix_cl_email_message_id", "cl_email", ["message_id"])
    op.create_index("ix_cl_email_to_index", "cl_email", ["to_index"])
    op.create_index("ix_cl_email_account_id", "cl_email", ["account_id"])
    op.create_index("ix_cl_email_prospect_id", "cl_email", ["prospect_id"])

    op.execute(sa.text("UPDATE cl_config SET value = :new WHERE key = 'contact_whatsapp' AND value = :old")
               .bindparams(new=NEW_NUMBER, old=OLD_NUMBER))


def downgrade() -> None:
    op.execute(sa.text("UPDATE cl_config SET value = :old WHERE key = 'contact_whatsapp' AND value = :new")
               .bindparams(new=NEW_NUMBER, old=OLD_NUMBER))
    for ix in ("ix_cl_email_prospect_id", "ix_cl_email_account_id", "ix_cl_email_to_index", "ix_cl_email_message_id"):
        op.drop_index(ix, table_name="cl_email")
    for col in ("sent_by", "prospect_id", "account_id", "to_index", "message_id"):
        op.drop_column("cl_email", col)
    op.drop_table("cl_inbound")
    op.drop_table("cl_channel")
