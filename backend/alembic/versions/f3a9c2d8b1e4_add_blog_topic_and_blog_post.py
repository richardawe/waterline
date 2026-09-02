"""add blog_topic and blog_post

Revision ID: f3a9c2d8b1e4
Revises: c17443101dd9
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a9c2d8b1e4'
down_revision: Union[str, None] = 'c17443101dd9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('blog_topic',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('prompt', sa.Text(), nullable=False),
    sa.Column('category', sa.String(length=80), nullable=False),
    sa.Column('target_keywords', sa.String(length=500), nullable=True),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('blog_post',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('topic_id', sa.String(length=64), nullable=True),
    sa.Column('slug', sa.String(length=200), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('meta_description', sa.String(length=200), nullable=False),
    sa.Column('excerpt', sa.Text(), nullable=False),
    sa.Column('tags_json', sa.Text(), nullable=True),
    sa.Column('content_markdown', sa.Text(), nullable=False),
    sa.Column('content_html', sa.Text(), nullable=True),
    sa.Column('faq_json', sa.Text(), nullable=True),
    sa.Column('news_refs_json', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('writer_model', sa.String(length=120), nullable=True),
    sa.Column('qa_model', sa.String(length=120), nullable=True),
    sa.Column('qa_verdict_json', sa.Text(), nullable=True),
    sa.Column('qa_attempts', sa.Integer(), nullable=False),
    sa.Column('word_count', sa.Integer(), nullable=False),
    sa.Column('reading_minutes', sa.Integer(), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['topic_id'], ['blog_topic.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )


def downgrade() -> None:
    op.drop_table('blog_post')
    op.drop_table('blog_topic')
