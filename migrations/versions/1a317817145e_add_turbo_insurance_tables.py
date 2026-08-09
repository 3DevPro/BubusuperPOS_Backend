"""add turbo insurance tables

Revision ID: 1a317817145e
Revises: e5ca9cf0ae7f
Create Date: 2026-08-09 00:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '1a317817145e'
down_revision: Union[str, None] = 'e5ca9cf0ae7f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Fixed ids so the catalog is stable across environments (dev/test/prod all
# get the same 4 rows on migrate, referenced by `code` everywhere else).
_PRODUCTS = [
    {
        "id": "11111111-1111-4111-8111-111111111101",
        "code": "daily_income",
        "kind": "daily_income",
        "name": "ชดเชยรายได้รายวัน",
        "description": "ชดเชยเมื่อร้านปิดจากเจ็บป่วยหรืออุบัติเหตุ คำนวณเบี้ยจากรายได้เฉลี่ยจริงของร้าน",
        "flat_monthly_premium": "0",
    },
    {
        "id": "11111111-1111-4111-8111-111111111102",
        "code": "accident",
        "kind": "accident",
        "name": "ไมโครประกันอุบัติเหตุ",
        "description": "คุ้มครองอุบัติเหตุส่วนบุคคล",
        "flat_monthly_premium": "149",
    },
    {
        "id": "11111111-1111-4111-8111-111111111103",
        "code": "health",
        "kind": "health",
        "name": "สุขภาพเหมาจ่ายวงเงินเล็ก",
        "description": "ค่ารักษาพยาบาลเหมาจ่ายวงเงินเล็ก",
        "flat_monthly_premium": "400",
    },
    {
        "id": "11111111-1111-4111-8111-111111111104",
        "code": "property",
        "kind": "property",
        "name": "ทรัพย์สินร้านค้า / รถเข็น",
        "description": "คุ้มครองทรัพย์สินร้านค้าและรถเข็นจากความเสียหาย",
        "flat_monthly_premium": "300",
    },
]


def upgrade() -> None:
    op.create_table(
        'turbo_insurance_products',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column(
            'kind',
            sa.Enum('daily_income', 'accident', 'health', 'property', name='insuranceproductkind'),
            nullable=False,
        ),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=False),
        sa.Column('flat_monthly_premium', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )

    op.create_table(
        'turbo_insurance_policies',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('product_id', sa.UUID(), nullable=False),
        sa.Column('daily_benefit', sa.Numeric(precision=12, scale=2), server_default='0', nullable=False),
        sa.Column('premium_amount', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('premium_cycle', sa.String(length=16), server_default='monthly', nullable=False),
        sa.Column(
            'status',
            sa.Enum('active', 'cancelled', 'expired', name='insurancepolicystatus'),
            nullable=False,
        ),
        sa.Column('income_profile_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['product_id'], ['turbo_insurance_products.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_turbo_insurance_policies_tenant_id'), 'turbo_insurance_policies', ['tenant_id'])

    op.create_table(
        'turbo_insurance_claims',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('policy_id', sa.UUID(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('days', sa.Integer(), nullable=False),
        sa.Column('benefit_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            'status',
            sa.Enum('pending', 'approved', 'rejected', name='insuranceclaimstatus'),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['policy_id'], ['turbo_insurance_policies.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_turbo_insurance_claims_tenant_id'), 'turbo_insurance_claims', ['tenant_id'])

    products_table = sa.table(
        'turbo_insurance_products',
        sa.column('id', sa.UUID()),
        sa.column('code', sa.String()),
        sa.column(
            'kind', sa.Enum('daily_income', 'accident', 'health', 'property', name='insuranceproductkind')
        ),
        sa.column('name', sa.String()),
        sa.column('description', sa.String()),
        sa.column('flat_monthly_premium', sa.Numeric()),
    )
    op.bulk_insert(products_table, _PRODUCTS)


def downgrade() -> None:
    op.drop_index(op.f('ix_turbo_insurance_claims_tenant_id'), table_name='turbo_insurance_claims')
    op.drop_table('turbo_insurance_claims')
    op.drop_index(op.f('ix_turbo_insurance_policies_tenant_id'), table_name='turbo_insurance_policies')
    op.drop_table('turbo_insurance_policies')
    op.drop_table('turbo_insurance_products')
    sa.Enum(name='insuranceclaimstatus').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='insurancepolicystatus').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='insuranceproductkind').drop(op.get_bind(), checkfirst=True)
