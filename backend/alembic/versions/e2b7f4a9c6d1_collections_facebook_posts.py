"""collections: Facebook Page posts written in the ops console

- cl_fb_post: text, optional link and image, schedule, approval and
  publishing state. The Page connection itself lives in cl_channel
  ("facebook"), like email and WhatsApp.

Revision ID: e2b7f4a9c6d1
Revises: d8a4c7e2b9f1
Create Date: 2026-11-13 16:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e2b7f4a9c6d1'
down_revision: Union[str, None] = 'd8a4c7e2b9f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cl_fb_post",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("link", sa.String(500), nullable=True),
        sa.Column("image_key", sa.String(200), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="Draft"),
        sa.Column("fb_post_id", sa.String(100), nullable=True),
        sa.Column("permalink", sa.String(500), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("approved_by", sa.String(64), nullable=True),
        sa.Column("published_by", sa.String(64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_cl_fb_post_status", "cl_fb_post", ["status"])


def downgrade() -> None:
    op.drop_table("cl_fb_post")
