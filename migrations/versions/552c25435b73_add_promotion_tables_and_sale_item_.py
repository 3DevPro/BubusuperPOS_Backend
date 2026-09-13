"""add promotion tables and sale item promo columns

Revision ID: 552c25435b73
Revises: 9c4b1f7ad203
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '552c25435b73'
down_revision: Union[str, None] = '9c4b1f7ad203'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'promotions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column(
            'kind',
            sa.Enum('percent_off', 'amount_off', 'buy_n_get_m', 'buy_n_for_price', name='promotionkind'),
            nullable=False,
        ),
        sa.Column(
            'scope', sa.Enum('product', 'category', 'all', name='promotionscope'), server_default='product', nullable=False
        ),
        sa.Column('percent_off', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('amount_off', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('buy_qty', sa.Integer(), nullable=True),
        sa.Column('get_qty', sa.Integer(), nullable=True),
        sa.Column('bundle_price', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('coupon_code', sa.String(length=32), nullable=True),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('active_weekdays', sa.String(length=7), nullable=True),
        sa.Column('daily_start_time', sa.Time(), nullable=True),
        sa.Column('daily_end_time', sa.Time(), nullable=True),
        sa.Column('max_uses', sa.Integer(), nullable=True),
        sa.Column('uses_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('priority', sa.Integer(), server_default='100', nullable=False),
        sa.Column('stackable', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'coupon_code', name='uq_promotions_tenant_coupon_code'),
    )
    op.create_index(op.f('ix_promotions_tenant_id'), 'promotions', ['tenant_id'], unique=False)

    op.create_table(
        'promotion_targets',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('promotion_id', sa.UUID(), nullable=False),
        sa.Column('product_id', sa.UUID(), nullable=True),
        sa.Column('category_id', sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['promotion_id'], ['promotions.id']),
        sa.ForeignKeyConstraint(['product_id'], ['products.id']),
        sa.ForeignKeyConstraint(['category_id'], ['categories.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_promotion_targets_tenant_id'), 'promotion_targets', ['tenant_id'], unique=False)
    op.create_index(
        op.f('ix_promotion_targets_promotion_id'), 'promotion_targets', ['promotion_id'], unique=False
    )

    op.add_column('sale_items', sa.Column('promo_discount', sa.Numeric(precision=12, scale=2), server_default='0', nullable=False))
    op.add_column('sale_items', sa.Column('promotion_id', sa.UUID(), nullable=True))
    op.add_column('sale_items', sa.Column('promotion_name_snapshot', sa.String(length=255), nullable=True))
    op.add_column('sale_items', sa.Column('is_free_gift', sa.Boolean(), server_default='false', nullable=False))
    op.create_foreign_key(
        'fk_sale_items_promotion_id', 'sale_items', 'promotions', ['promotion_id'], ['id']
    )


def downgrade() -> None:
    op.drop_constraint('fk_sale_items_promotion_id', 'sale_items', type_='foreignkey')
    op.drop_column('sale_items', 'is_free_gift')
    op.drop_column('sale_items', 'promotion_name_snapshot')
    op.drop_column('sale_items', 'promotion_id')
    op.drop_column('sale_items', 'promo_discount')

    op.drop_index(op.f('ix_promotion_targets_promotion_id'), table_name='promotion_targets')
    op.drop_index(op.f('ix_promotion_targets_tenant_id'), table_name='promotion_targets')
    op.drop_table('promotion_targets')

    op.drop_index(op.f('ix_promotions_tenant_id'), table_name='promotions')
    op.drop_table('promotions')
    sa.Enum(name='promotionscope').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='promotionkind').drop(op.get_bind(), checkfirst=True)
