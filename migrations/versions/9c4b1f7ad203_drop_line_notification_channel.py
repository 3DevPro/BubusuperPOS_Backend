"""drop line notification channel

The LINE OA is retired — the whole channel (service, endpoints, tables and
the enum value) is removed rather than left switched off. Any historical
`line` delivery rows go with it; the Notification inbox rows they belonged
to are untouched, since those are created independently of channel
availability.

Postgres can't remove a value from an existing enum type, so
`notificationchannelname` is recreated without `line` and the column
re-pointed at the new type.

Revision ID: 9c4b1f7ad203
Revises: 2490240ab65f
Create Date: 2026-08-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c4b1f7ad203'
down_revision: Union[str, None] = '2490240ab65f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f('ix_line_recipients_tenant_id'), table_name='line_recipients')
    op.drop_table('line_recipients')

    op.drop_index(op.f('ix_line_link_tokens_tenant_id'), table_name='line_link_tokens')
    op.drop_table('line_link_tokens')

    op.drop_column('notification_settings', 'line_enabled')

    op.execute("DELETE FROM notification_deliveries WHERE channel = 'line'")
    op.execute("ALTER TYPE notificationchannelname RENAME TO notificationchannelname_old")
    op.execute("CREATE TYPE notificationchannelname AS ENUM ('inapp')")
    op.execute(
        "ALTER TABLE notification_deliveries ALTER COLUMN channel "
        "TYPE notificationchannelname USING channel::text::notificationchannelname"
    )
    op.execute("DROP TYPE notificationchannelname_old")


def downgrade() -> None:
    # Restores the schema, not the data — linked LINE accounts and the
    # delivery history dropped above are gone for good.
    op.execute("ALTER TYPE notificationchannelname RENAME TO notificationchannelname_old")
    op.execute("CREATE TYPE notificationchannelname AS ENUM ('inapp', 'line')")
    op.execute(
        "ALTER TABLE notification_deliveries ALTER COLUMN channel "
        "TYPE notificationchannelname USING channel::text::notificationchannelname"
    )
    op.execute("DROP TYPE notificationchannelname_old")

    op.add_column(
        'notification_settings',
        sa.Column('line_enabled', sa.Boolean(), server_default='false', nullable=False),
    )

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
