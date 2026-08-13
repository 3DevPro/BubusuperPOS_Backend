"""add line tables

Revision ID: 2490240ab65f
Revises: 852f18aa0435
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2490240ab65f'
down_revision: Union[str, None] = '852f18aa0435'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'line_link_tokens',
        sa.Column('token', sa.String(length=32), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('token'),
    )
    op.create_index(op.f('ix_line_link_tokens_tenant_id'), 'line_link_tokens', ['tenant_id'], unique=False)

    op.create_table(
        'line_recipients',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('line_user_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('display_name', sa.String(length=255), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('linked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('line_user_id', name='uq_line_recipients_line_user_id'),
    )
    op.create_index(op.f('ix_line_recipients_tenant_id'), 'line_recipients', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_line_recipients_tenant_id'), table_name='line_recipients')
    op.drop_table('line_recipients')

    op.drop_index(op.f('ix_line_link_tokens_tenant_id'), table_name='line_link_tokens')
    op.drop_table('line_link_tokens')
