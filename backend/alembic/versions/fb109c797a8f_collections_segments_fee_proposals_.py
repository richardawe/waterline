"""collections: client segments, fee schedules + proposals, waitlist, balance updates

Makes Collections multi-segment (schools, estates live; lenders and service
businesses on a waitlist) — see app/collections/segments.py.

Data steps:
- cl_client.fee_percent (flat) → cl_client.fee_schedule JSON {"type": "flat", "percent": …}
- cl_client.segment from the old business_type (Estate → estate; School → school;
  Gym / Internet provider / Other → service), and business_type renamed to the
  segment's client type
- existing templates are the school/estate wording → tagged segment 'school'
  (other segments seed their own defaults on first use)

Encrypted columns are plain Text at the database level.

Revision ID: fb109c797a8f
Revises: e0d3f5700598
Create Date: 2026-10-10 16:02:31.156427

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'fb109c797a8f'
down_revision: Union[str, None] = 'e0d3f5700598'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'cl_segment',
        sa.Column('key', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('policy_overrides', sa.Text(), nullable=True),
        sa.Column('default_fee_schedule', sa.Text(), nullable=True),
        sa.Column('policy_reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('policy_reviewed_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )
    op.create_table(
        'cl_waitlist',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('segment', sa.String(length=20), nullable=False),
        sa.Column('business_name', sa.String(length=200), nullable=False),
        sa.Column('business_type', sa.String(length=60), nullable=True),
        sa.Column('contact_name', sa.String(length=160), nullable=False),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('phone', sa.Text(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cl_waitlist_segment', 'cl_waitlist', ['segment'])
    op.create_table(
        'cl_fee_proposal',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('client_id', sa.String(length=64), nullable=False),
        sa.Column('schedule', sa.Text(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('proposed_by', sa.String(length=64), nullable=True),
        sa.Column('decided_by', sa.String(length=64), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decision_note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['cl_client.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cl_fee_proposal_client_id', 'cl_fee_proposal', ['client_id'])

    # clients: segment, fee schedule, segment-specific profile
    op.add_column('cl_client', sa.Column('segment', sa.String(length=20), nullable=False, server_default='school'))
    op.add_column('cl_client', sa.Column('fee_schedule', sa.Text(), nullable=True))
    op.add_column('cl_client', sa.Column('profile_extra', sa.Text(), nullable=True))
    op.execute("""
        UPDATE cl_client SET
          segment = CASE business_type WHEN 'Estate' THEN 'estate' WHEN 'School' THEN 'school' ELSE 'service' END,
          business_type = CASE business_type
            WHEN 'Estate' THEN 'Estate management'
            WHEN 'School' THEN 'Other school'
            WHEN 'Gym' THEN 'Gym or club'
            WHEN 'Internet provider' THEN 'Internet provider'
            ELSE 'Other service business' END,
          fee_schedule = json_build_object('type', 'flat', 'percent', fee_percent::float)::text
    """)
    op.alter_column('cl_client', 'fee_schedule', nullable=False)
    op.alter_column('cl_client', 'segment', server_default=None)
    op.create_index('ix_cl_client_segment', 'cl_client', ['segment'])
    op.drop_column('cl_client', 'fee_percent')

    # accounts: the client's own reference + segment-specific values
    op.add_column('cl_debtor_account', sa.Column('external_reference', sa.Text(), nullable=True))
    op.add_column('cl_debtor_account', sa.Column('external_ref_index', sa.String(length=64), nullable=True))
    op.add_column('cl_debtor_account', sa.Column('extra', sa.Text(), nullable=True))
    op.create_index('ix_cl_debtor_account_external_ref_index', 'cl_debtor_account', ['external_ref_index'])

    # fee rate actually applied (tiered schedules vary per payment)
    op.add_column('cl_payment', sa.Column('fee_percent', sa.Numeric(precision=5, scale=2), nullable=True))
    op.add_column('cl_invoice_line', sa.Column('fee_percent', sa.Numeric(precision=5, scale=2), nullable=True))
    op.execute("""
        UPDATE cl_payment p SET fee_percent = ROUND(p.fee_amount * 100.0 / NULLIF(p.amount_confirmed, 0), 2)
        WHERE p.fee_eligible
    """)
    op.execute("""
        UPDATE cl_invoice_line l SET fee_percent = ROUND(l.fee * 100.0 / NULLIF(l.amount, 0), 2)
    """)

    # uploads: new accounts vs balance updates
    op.add_column('cl_upload_batch', sa.Column('mode', sa.String(length=10), nullable=False, server_default='new'))
    op.alter_column('cl_upload_batch', 'mode', server_default=None)

    # templates per segment
    op.add_column('cl_message_template', sa.Column('segment', sa.String(length=20), nullable=False, server_default='school'))
    op.alter_column('cl_message_template', 'segment', server_default=None)
    op.drop_constraint('uq_cl_template_version', 'cl_message_template', type_='unique')
    op.create_unique_constraint('uq_cl_template_version', 'cl_message_template', ['segment', 'key', 'language', 'version'])


def downgrade() -> None:
    op.drop_constraint('uq_cl_template_version', 'cl_message_template', type_='unique')
    op.execute("DELETE FROM cl_message_template WHERE segment <> 'school'")
    op.create_unique_constraint('uq_cl_template_version', 'cl_message_template', ['key', 'language', 'version'])
    op.drop_column('cl_message_template', 'segment')
    op.drop_column('cl_upload_batch', 'mode')
    op.drop_column('cl_invoice_line', 'fee_percent')
    op.drop_column('cl_payment', 'fee_percent')
    op.drop_index('ix_cl_debtor_account_external_ref_index', table_name='cl_debtor_account')
    op.drop_column('cl_debtor_account', 'extra')
    op.drop_column('cl_debtor_account', 'external_ref_index')
    op.drop_column('cl_debtor_account', 'external_reference')
    op.add_column('cl_client', sa.Column('fee_percent', sa.Numeric(precision=5, scale=2), nullable=False, server_default='20'))
    # a tiered schedule has no single rate; fall back to its first tier
    op.execute("""
        UPDATE cl_client SET fee_percent = COALESCE(
          (fee_schedule::json->>'percent')::numeric,
          (fee_schedule::json->'tiers'->0->>'percent')::numeric, 20)
    """)
    op.alter_column('cl_client', 'fee_percent', server_default=None)
    op.execute("""
        UPDATE cl_client SET business_type = CASE segment
          WHEN 'estate' THEN 'Estate' WHEN 'school' THEN 'School' ELSE 'Other' END
    """)
    op.drop_index('ix_cl_client_segment', table_name='cl_client')
    op.drop_column('cl_client', 'profile_extra')
    op.drop_column('cl_client', 'fee_schedule')
    op.drop_column('cl_client', 'segment')
    op.drop_index('ix_cl_fee_proposal_client_id', table_name='cl_fee_proposal')
    op.drop_table('cl_fee_proposal')
    op.drop_index('ix_cl_waitlist_segment', table_name='cl_waitlist')
    op.drop_table('cl_waitlist')
    op.drop_table('cl_segment')
