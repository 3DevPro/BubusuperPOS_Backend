"""add turbo_daily_closes table

Revision ID: e5ca9cf0ae7f
Revises: f99196da8d86
Create Date: 2026-08-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5ca9cf0ae7f'
down_revision: Union[str, None] = 'f99196da8d86'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'turbo_daily_closes',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('business_date', sa.Date(), nullable=False),
        sa.Column(
            'closed_reason',
            sa.Enum('open', 'sick', 'accident', 'holiday', 'other', name='dailyclosereason'),
            nullable=False,
        ),
        sa.Column('extra_expense', sa.Numeric(precision=12, scale=2), server_default='0', nullable=False),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'business_date', name='uq_turbo_daily_closes_tenant_date'),
    )
    op.create_index(
        op.f('ix_turbo_daily_closes_tenant_id'), 'turbo_daily_closes', ['tenant_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_turbo_daily_closes_tenant_id'), table_name='turbo_daily_closes')
    op.drop_table('turbo_daily_closes')
    # Autogenerate's drop_table doesn't drop the Postgres enum type the
    # dropped column used — left behind, it collides with the CREATE TYPE a
    # future re-upgrade issues (same fix as purchaseorderstatus's downgrade).
    sa.Enum(name='dailyclosereason').drop(op.get_bind(), checkfirst=True)
