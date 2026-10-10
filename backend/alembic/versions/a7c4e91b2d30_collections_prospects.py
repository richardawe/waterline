"""collections: prospects (Waterline's own sales outreach) + "First 20 Prospects"

Adds cl_prospect_list, cl_prospect and cl_prospect_touch, and loads the
first prospect list researched on 10 Oct 2026. Every contact is an
organisation's published business address or line; named heads come from
school pages or Wikipedia and may be out of date, so `contact_confirmed`
starts false and messages greet the role ("Dear Head of School") until ops
confirm the name.

Revision ID: a7c4e91b2d30
Revises: fb109c797a8f
Create Date: 2026-10-10 20:40:00

"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a7c4e91b2d30'
down_revision: Union[str, None] = 'fb109c797a8f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LIST_NAME = "Waterline Collections — First 20 Prospects"
LIST_DESCRIPTION = (
    "10 Oct 2026 · Richard Awe. Schools: fourteen schools in Lagos and Abuja with publicly listed contacts; only "
    "official school addresses are listed, no personal emails. Names of heads come from school pages or Wikipedia "
    "and may be out of date, so confirm before addressing anyone by name. Mid-fee schools are the likelier quick "
    "adopters; elite international schools are included as reference accounts but tend to have fewer arrears. "
    "Estates: facility and property managers that run residential estates with service charges."
)

# segment, org_name, org_type, city, address, contact_name, contact_role, email, phone, phone_note, fit, fit_note, source, notes
PROSPECTS = [
    ("school", "Corona School Lekki", "School (multi-campus group)", "Lagos", None, "Mrs Grace Omo-Egbekuseo", "Head of School",
     "lks@coronaschools.org", "+2347072713342", None, "High", "Multi-campus group with a business office", "coronaschools.org",
     "Group address: info@coronaschools.org"),
    ("school", "Grace Schools, Gbagada", "School", "Lagos", None, "Dr Olanike Akindayo", "Head of School",
     "admissions@graceschools.net", "+2348023061195", "Admin line, on WhatsApp", "High", "Long-established, mid-to-upper fees",
     "graceschools.net, Wikipedia", None),
    ("school", "Meadow Hall, Lekki", "School (group, about 1,400 students across sites)", "Lagos", None, "Kehinde Nwani", "Founder",
     "admissions@meadowhalleducation.org", "+2348073000554", None, "High", "About 1,400 students across sites",
     "Fact sheet, Jennifer Agbeyi, finelib",
     "Also info@meadowhallschool.org. Jennifer Agbeyi is Head of Junior School."),
    ("school", "Meadow Hall, Ikoyi", "School (same group, second site)", "Lagos", None, None, None,
     "info_ikoyi@meadowhallschool.org", "+2348072263466", None, "High", "Same group, second site", "meadowhalleducation.org", None),
    ("school", "Greensprings School, Anthony", "School (large group)", "Lagos", None, "Mrs Magdalene Okrikri", "Head of School",
     "info.anthony@greenspringsschool.com", "+2349087171700", None, "Medium", "Large group, may have in-house finance team",
     "greenspringsschool.com", "Head's date of appointment unclear."),
    ("school", "Greensprings School, Lekki", "School (large group)", "Lagos", None, None, None,
     "info.lekki@greenspringsschool.com", "+2349129534036", None, "Medium", None, "greenspringsschool.com", None),
    ("school", "Greensprings Montessori College, Gbagada", "School (large group)", "Lagos", None, None, None,
     "gmc@greenspringsschool.com", "+2348023438980", None, "Medium", None, "gmc.greenspringsschool.com", None),
    ("school", "Avi-Cenna International School, Ikeja GRA", "School", "Lagos", None, "Paul Olubajo", "Principal",
     None, None, None, "Medium", None, "Wikipedia", "No email or phone found yet."),
    ("school", "Lagos Preparatory School, Ikoyi", "School", "Lagos", None, "John Samuel", "Head of School",
     None, None, None, "Low", "Fees N2.1m to N3.9m a year, likely few arrears", "Wikipedia", "No email or phone found yet."),
    ("school", "Stella Maris Schools", "School (three campuses since 1996)", "Abuja", None, None, None,
     "info@smsabuja.sch.ng", "+2349169182000", None, "High", "Three campuses since 1996", "smsabuja.com", None),
    ("school", "The Regent School", "School", "Abuja", None, None, None,
     None, "+2348072293289", "Registrar", "Medium", None, "regentschoolabuja.com", "Email hidden on site; use the registrar line."),
    ("school", "American International School of Abuja", "School (non-profit)", "Abuja", None, "Greg Hughes", "Head of School",
     "admissions@aisabuja.com", "+2347032153798", None, "Low", "Non-profit, mostly expatriate families",
     "US State Dept fact sheet, Wikipedia", None),
    ("school", "Cherryfield Nursery & Primary School, Gwarimpa", "Nursery and primary school", "Abuja", None, None, None,
     None, "+2348036281367", None, "High", "Mid-fee, likely manual fee chasing", "Takooka", None),
    ("school", "Start Rite Nursery & Primary School, Apo", "Nursery and primary school", "Abuja", None, None, None,
     None, "+2348106334006", None, "High", "Mid-fee", "Takooka", None),
    ("estate", "Briscoe Properties Limited",
     "Facility manager; runs Fara Park Estate, Ajah (304 units) and Eko Court, VI (155 apartments)", "Lagos", None, None, None,
     "bpl@rtbriscoe.com", "+2347074754362", None, "High", "Named residential estates with service charges",
     "briscoeproperties.com", None),
    ("estate", "Jide Taiwo & Co", "Property and facility manager; lists service charge administration and gated estates",
     "Lagos", "5 Banford Williams Close, Victoria Island", None, None,
     None, None, None, "High", "Service charge collection is a stated service", "jidetaiwoandco.com",
     "No email or phone on the page; office address only."),
    ("estate", "Essential Facility Management", "Facility manager, Lagos and Abuja", "Lagos and Abuja", None, None, None,
     "info@essentialfacilitymgt.com", "+2348066030376", "On WhatsApp", "High", "Residential focus in Lekki, VI, Ajah",
     "essentialfacilitymgt.com", None),
    ("estate", "Sapphire Facility Managers", "Facility and property manager", None, None, None, None,
     "info@sapphiremanagers.org", "+2347033894813", None, "Medium", None, "sapphiremanagers.org", None),
    ("estate", "Eliezer Facility Management", "Facility manager, mostly corporate", None, None, None, None,
     "info@eliezergroup.com", "+2348053543937", None, "Medium", "Corporate clients, fewer household arrears",
     "eliezergroup.com", None),
    ("service", "Skyfit Lagos", "Gym, Victoria Island", "Lagos", None, None, None,
     "getfit@skyfitnessng.com", None, None, "Low", "Gyms mostly sell prepaid memberships, so unpaid balances are rare",
     "connectciti listing", "Gyms aren't live yet: invite to the waitlist rather than sign-up."),
]


def upgrade() -> None:
    op.create_table(
        'cl_prospect_list',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    prospect = op.create_table(
        'cl_prospect',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('list_id', sa.String(length=64), nullable=True),
        sa.Column('segment', sa.String(length=20), nullable=False),
        sa.Column('org_name', sa.String(length=200), nullable=False),
        sa.Column('org_type', sa.String(length=300), nullable=True),
        sa.Column('city', sa.String(length=80), nullable=True),
        sa.Column('address', sa.String(length=300), nullable=True),
        sa.Column('contact_name', sa.String(length=160), nullable=True),
        sa.Column('contact_role', sa.String(length=120), nullable=True),
        sa.Column('contact_confirmed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('email', sa.String(length=254), nullable=True),
        sa.Column('phone', sa.String(length=20), nullable=True),
        sa.Column('phone_note', sa.String(length=120), nullable=True),
        sa.Column('fit', sa.String(length=10), nullable=True),
        sa.Column('fit_note', sa.String(length=300), nullable=True),
        sa.Column('source', sa.String(length=300), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='New'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('next_follow_up', sa.Date(), nullable=True),
        sa.Column('last_contacted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['list_id'], ['cl_prospect_list.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_cl_prospect_list_id'), 'cl_prospect', ['list_id'], unique=False)
    op.create_index(op.f('ix_cl_prospect_segment'), 'cl_prospect', ['segment'], unique=False)
    op.create_index(op.f('ix_cl_prospect_status'), 'cl_prospect', ['status'], unique=False)
    op.create_table(
        'cl_prospect_touch',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('prospect_id', sa.String(length=64), nullable=False),
        sa.Column('channel', sa.String(length=10), nullable=False),
        sa.Column('template_key', sa.String(length=20), nullable=True),
        sa.Column('subject', sa.String(length=300), nullable=True),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('ops_user_id', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['prospect_id'], ['cl_prospect.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_cl_prospect_touch_prospect_id'), 'cl_prospect_touch', ['prospect_id'], unique=False)

    list_id = str(uuid.uuid4())
    plist = sa.table('cl_prospect_list', sa.column('id', sa.String), sa.column('name', sa.String),
                     sa.column('description', sa.Text))
    op.bulk_insert(plist, [{"id": list_id, "name": LIST_NAME, "description": LIST_DESCRIPTION}])
    keys = ("segment", "org_name", "org_type", "city", "address", "contact_name", "contact_role", "email", "phone",
            "phone_note", "fit", "fit_note", "source", "notes")
    op.bulk_insert(prospect, [
        {"id": str(uuid.uuid4()), "list_id": list_id, "status": "New", "contact_confirmed": False, **dict(zip(keys, row))}
        for row in PROSPECTS
    ])


def downgrade() -> None:
    op.drop_index(op.f('ix_cl_prospect_touch_prospect_id'), table_name='cl_prospect_touch')
    op.drop_table('cl_prospect_touch')
    op.drop_index(op.f('ix_cl_prospect_status'), table_name='cl_prospect')
    op.drop_index(op.f('ix_cl_prospect_segment'), table_name='cl_prospect')
    op.drop_index(op.f('ix_cl_prospect_list_id'), table_name='cl_prospect')
    op.drop_table('cl_prospect')
    op.drop_table('cl_prospect_list')
