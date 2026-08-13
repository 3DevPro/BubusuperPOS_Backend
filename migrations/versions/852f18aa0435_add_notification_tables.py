"""add notification tables

Revision ID: 852f18aa0435
Revises: ab529c0d8174
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '852f18aa0435'
down_revision: Union[str, None] = 'ab529c0d8174'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notifications',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('kind', sa.Enum('low_stock', 'daily_summary', 'system', name='notificationkind'), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('body', sa.String(length=2000), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('dedupe_key', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'dedupe_key', name='uq_notifications_tenant_dedupe'),
    )
    op.create_index(op.f('ix_notifications_tenant_id'), 'notifications', ['tenant_id'], unique=False)

    op.create_table(
        'notification_settings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('low_stock_enabled', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('low_stock_time', sa.Time(), server_default='09:00:00', nullable=False),
        sa.Column('low_stock_repeat_days', sa.Integer(), server_default='7', nullable=False),
        sa.Column('daily_summary_enabled', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('daily_summary_time', sa.Time(), server_default='20:00:00', nullable=False),
        sa.Column('quiet_hours_start', sa.Time(), nullable=True),
        sa.Column('quiet_hours_end', sa.Time(), nullable=True),
        sa.Column('line_enabled', sa.Boolean(), server_default='false', nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', name='uq_notification_settings_tenant'),
    )
    op.create_index(
        op.f('ix_notification_settings_tenant_id'), 'notification_settings', ['tenant_id'], unique=False
    )

    op.create_table(
        'notification_deliveries',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('notification_id', sa.UUID(), nullable=False),
        sa.Column('channel', sa.Enum('inapp', 'line', name='notificationchannelname'), nullable=False),
        sa.Column('recipient', sa.String(length=64), nullable=True),
        sa.Column(
            'status',
            sa.Enum('pending', 'sent', 'failed', 'skipped', name='deliverystatus'),
            server_default='pending',
            nullable=False,
        ),
        sa.Column('not_before', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['notification_id'], ['notifications.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'notification_id', 'channel', 'recipient', name='uq_notification_deliveries_channel_recipient'
        ),
    )
    op.create_index(
        op.f('ix_notification_deliveries_tenant_id'), 'notification_deliveries', ['tenant_id'], unique=False
    )
    op.create_index(
        op.f('ix_notification_deliveries_notification_id'),
        'notification_deliveries',
        ['notification_id'],
        unique=False,
    )

    op.create_table(
        'low_stock_alert_state',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('product_id', sa.UUID(), nullable=False),
        sa.Column('last_alerted_on', sa.Date(), nullable=False),
        sa.Column('last_alerted_qty', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['product_id'], ['products.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'product_id', name='uq_low_stock_alert_state_tenant_product'),
    )
    op.create_index(
        op.f('ix_low_stock_alert_state_tenant_id'), 'low_stock_alert_state', ['tenant_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_low_stock_alert_state_tenant_id'), table_name='low_stock_alert_state')
    op.drop_table('low_stock_alert_state')

    op.drop_index(op.f('ix_notification_deliveries_notification_id'), table_name='notification_deliveries')
    op.drop_index(op.f('ix_notification_deliveries_tenant_id'), table_name='notification_deliveries')
    op.drop_table('notification_deliveries')
    sa.Enum(name='deliverystatus').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='notificationchannelname').drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f('ix_notification_settings_tenant_id'), table_name='notification_settings')
    op.drop_table('notification_settings')

    op.drop_index(op.f('ix_notifications_tenant_id'), table_name='notifications')
    op.drop_table('notifications')
    sa.Enum(name='notificationkind').drop(op.get_bind(), checkfirst=True)
