"""collections: ops team expense claims and payout bank details

- cl_expense: money a team member spent for Waterline and wants paid back.
  Submitted → Approved → Paid, or Rejected. Receipts live in private storage.
- cl_ops_user.payout_details: the team member's bank details (encrypted
  JSON) so an Admin knows where to send the money.

Revision ID: d8a4c7e2b9f1
Revises: c5f2a9e1d6b3
Create Date: 2026-11-02 16:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd8a4c7e2b9f1'
down_revision: Union[str, None] = 'c5f2a9e1d6b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cl_expense",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("ops_user_id", sa.String(64), sa.ForeignKey("cl_ops_user.id"), nullable=False),
        sa.Column("spent_on", sa.Date(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("receipt_key", sa.String(200), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="Submitted"),
        sa.Column("decided_by", sa.String(64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("paid_by", sa.String(64), nullable=True),
        sa.Column("paid_on", sa.Date(), nullable=True),
        sa.Column("paid_reference", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_cl_expense_ops_user_id", "cl_expense", ["ops_user_id"])
    op.create_index("ix_cl_expense_status", "cl_expense", ["status"])
    op.add_column("cl_ops_user", sa.Column("payout_details", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("cl_ops_user", "payout_details")
    op.drop_table("cl_expense")
