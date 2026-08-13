"""add barcode counter and unique index

Revision ID: ab529c0d8174
Revises: 0e1fcf93bb93
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ab529c0d8174'
down_revision: Union[str, None] = '0e1fcf93bb93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'tenants', sa.Column('internal_barcode_counter', sa.Integer(), nullable=False, server_default='0')
    )

    # products.barcode has never been constrained, so real (tenant_id,
    # barcode) duplicates — plus a scattering of '' instead of NULL — are
    # possible in existing data. Normalize '' to NULL, then for any
    # surviving duplicate group keep the oldest row and null out the rest,
    # so the unique index below can actually be created.
    op.execute("UPDATE products SET barcode = NULL WHERE barcode = ''")
    op.execute(
        """
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY tenant_id, barcode ORDER BY created_at ASC, id ASC
            ) AS rn
            FROM products
            WHERE barcode IS NOT NULL
        )
        UPDATE products SET barcode = NULL
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """
    )

    op.create_index(
        'uq_products_tenant_barcode',
        'products',
        ['tenant_id', 'barcode'],
        unique=True,
        postgresql_where=sa.text('barcode IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_products_tenant_barcode', table_name='products')
    op.drop_column('tenants', 'internal_barcode_counter')
