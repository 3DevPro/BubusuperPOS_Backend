"""add turbo loan tables

Revision ID: b7d4f2a91c3e
Revises: 2fbbe7ec560d
Create Date: 2026-08-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b7d4f2a91c3e'
down_revision: Union[str, None] = '2fbbe7ec560d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Fixed ids so the catalog is stable across environments, same rationale as
# _PRODUCTS in 1a317817145e_add_turbo_insurance_tables.py. Rates/ceilings are
# the case's demo-stage assumptions (see app/core/turbo_config.py's own
# docstring) — turbo.co.th's advertised 15-24%/year and 0.68%/month land-title
# starting rate, translated to a flat monthly_interest_rate per product.
_PRODUCTS = [
    {
        "id": "22222222-2222-4222-8222-222222222201",
        "code": "motorcycle",
        "collateral_kind": "motorcycle",
        "name": "สินเชื่อรถมอเตอร์ไซค์",
        "description": "ใช้มอเตอร์ไซค์เป็นหลักประกัน วงเงินไว ผ่อนสบาย",
        "max_principal": "100000",
        "monthly_interest_rate": "0.0200",
        "min_term_months": 6,
        "max_term_months": 36,
    },
    {
        "id": "22222222-2222-4222-8222-222222222202",
        "code": "car",
        "collateral_kind": "car",
        "name": "สินเชื่อรถยนต์",
        "description": "ใช้รถยนต์เป็นหลักประกัน วงเงินสูง ดอกเบี้ยเป็นมิตร",
        "max_principal": "1000000",
        "monthly_interest_rate": "0.0125",
        "min_term_months": 6,
        "max_term_months": 60,
    },
    {
        "id": "22222222-2222-4222-8222-222222222203",
        "code": "tractor",
        "collateral_kind": "tractor",
        "name": "สินเชื่อรถแทรกเตอร์",
        "description": "ใช้รถแทรกเตอร์หรือเครื่องจักรกลเกษตรเป็นหลักประกัน ไม่ต้องค้ำ รับเงินเร็ว",
        "max_principal": "500000",
        "monthly_interest_rate": "0.0142",
        "min_term_months": 6,
        "max_term_months": 48,
    },
    {
        "id": "22222222-2222-4222-8222-222222222204",
        "code": "land_title",
        "collateral_kind": "land_title",
        "name": "สินเชื่อโฉนดที่ดิน",
        "description": "ใช้โฉนดที่ดินเป็นหลักประกัน วงเงินสูงสุด ดอกเบี้ยต่ำที่สุด",
        "max_principal": "2000000",
        "monthly_interest_rate": "0.0068",
        "min_term_months": 12,
        "max_term_months": 120,
    },
]


def upgrade() -> None:
    op.create_table(
        'turbo_loan_products',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column(
            'collateral_kind',
            sa.Enum('motorcycle', 'car', 'tractor', 'land_title', name='loancollateralkind'),
            nullable=False,
        ),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=False),
        sa.Column('max_principal', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('monthly_interest_rate', sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column('min_term_months', sa.Integer(), server_default='6', nullable=False),
        sa.Column('max_term_months', sa.Integer(), server_default='36', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )

    op.create_table(
        'turbo_loan_applications',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('product_id', sa.UUID(), nullable=False),
        sa.Column('requested_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('collateral_value', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('term_months', sa.Integer(), nullable=False),
        sa.Column('approved_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('monthly_installment', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('monthly_interest_rate_snapshot', sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column('income_profile_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('credit_tier_snapshot', sa.String(length=16), nullable=False),
        sa.Column(
            'cap_reasons',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column('assigned_branch_id', sa.UUID(), nullable=True),
        sa.Column('lead_id', sa.UUID(), nullable=True),
        sa.Column(
            'status',
            sa.Enum('submitted', 'approved', 'disbursed', 'rejected', name='loanapplicationstatus'),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['product_id'], ['turbo_loan_products.id']),
        sa.ForeignKeyConstraint(['assigned_branch_id'], ['turbo_branches.id']),
        sa.ForeignKeyConstraint(['lead_id'], ['turbo_leads.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_turbo_loan_applications_tenant_id'), 'turbo_loan_applications', ['tenant_id'])

    op.create_table(
        'turbo_loan_accounts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('application_id', sa.UUID(), nullable=False),
        sa.Column('product_id', sa.UUID(), nullable=False),
        sa.Column('account_number', sa.String(length=32), nullable=False),
        sa.Column('principal', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('monthly_interest_rate', sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column('term_months', sa.Integer(), nullable=False),
        sa.Column('monthly_installment', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('status', sa.Enum('active', 'closed', name='loanaccountstatus'), nullable=False),
        sa.Column('disbursed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('first_due_date', sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['application_id'], ['turbo_loan_applications.id']),
        sa.ForeignKeyConstraint(['product_id'], ['turbo_loan_products.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('account_number'),
    )
    op.create_index(op.f('ix_turbo_loan_accounts_tenant_id'), 'turbo_loan_accounts', ['tenant_id'])

    op.create_table(
        'turbo_loan_installments',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('account_id', sa.UUID(), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=False),
        sa.Column('principal_component', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('interest_component', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('amount_due', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('status', sa.Enum('unpaid', 'paid', name='loaninstallmentstatus'), nullable=False),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('paid_amount', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('paid_reference', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['account_id'], ['turbo_loan_accounts.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('account_id', 'sequence', name='uq_turbo_loan_installments_account_seq'),
    )
    op.create_index(op.f('ix_turbo_loan_installments_tenant_id'), 'turbo_loan_installments', ['tenant_id'])

    products_table = sa.table(
        'turbo_loan_products',
        sa.column('id', sa.UUID()),
        sa.column('code', sa.String()),
        sa.column(
            'collateral_kind',
            sa.Enum('motorcycle', 'car', 'tractor', 'land_title', name='loancollateralkind'),
        ),
        sa.column('name', sa.String()),
        sa.column('description', sa.String()),
        sa.column('max_principal', sa.Numeric()),
        sa.column('monthly_interest_rate', sa.Numeric()),
        sa.column('min_term_months', sa.Integer()),
        sa.column('max_term_months', sa.Integer()),
    )
    op.bulk_insert(products_table, _PRODUCTS)

    # in_app: a tenant applying for a loan from inside the POS app, as
    # opposed to the public O2O quote form (o2o_web) — see LeadSource.
    op.execute("ALTER TYPE leadsource ADD VALUE IF NOT EXISTS 'in_app'")
    op.add_column('turbo_leads', sa.Column('quoted_loan_amount', sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column(
        'turbo_leads', sa.Column('quoted_monthly_installment', sa.Numeric(precision=12, scale=2), nullable=True)
    )
    op.add_column('turbo_leads', sa.Column('collateral_kind', sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column('turbo_leads', 'collateral_kind')
    op.drop_column('turbo_leads', 'quoted_monthly_installment')
    op.drop_column('turbo_leads', 'quoted_loan_amount')
    # Postgres can't drop a single enum value — downgrading past this
    # migration leaves 'in_app' in the type, same tradeoff as
    # 2fbbe7ec560d's 'branch_champion'.

    op.drop_index(op.f('ix_turbo_loan_installments_tenant_id'), table_name='turbo_loan_installments')
    op.drop_table('turbo_loan_installments')
    sa.Enum(name='loaninstallmentstatus').drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f('ix_turbo_loan_accounts_tenant_id'), table_name='turbo_loan_accounts')
    op.drop_table('turbo_loan_accounts')
    sa.Enum(name='loanaccountstatus').drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f('ix_turbo_loan_applications_tenant_id'), table_name='turbo_loan_applications')
    op.drop_table('turbo_loan_applications')
    sa.Enum(name='loanapplicationstatus').drop(op.get_bind(), checkfirst=True)

    op.drop_table('turbo_loan_products')
    sa.Enum(name='loancollateralkind').drop(op.get_bind(), checkfirst=True)
