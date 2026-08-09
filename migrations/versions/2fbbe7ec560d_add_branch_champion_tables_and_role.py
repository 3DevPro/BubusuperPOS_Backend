"""add branch/prospect/lead tables and branch_champion role

Revision ID: 2fbbe7ec560d
Revises: 1a317817145e
Create Date: 2026-08-09 00:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2fbbe7ec560d'
down_revision: Union[str, None] = '1a317817145e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A branch_champion has no tenant_id (see UserRole.branch_champion) — was
    # NOT NULL since the initial schema, so existing rows need no backfill,
    # only future ones are allowed to leave it null.
    op.alter_column('users', 'tenant_id', nullable=True)
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'branch_champion'")

    op.create_table(
        'turbo_branches',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('code', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('province', sa.String(length=100), nullable=False),
        sa.Column('lat', sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column('lng', sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )

    op.add_column('users', sa.Column('branch_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_users_branch_id'), 'users', ['branch_id'])
    op.create_foreign_key('fk_users_branch_id', 'users', 'turbo_branches', ['branch_id'], ['id'])

    op.create_table(
        'turbo_merchant_prospects',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('branch_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('business_type', sa.String(length=100), nullable=True),
        sa.Column('address', sa.String(length=500), nullable=True),
        sa.Column('phone', sa.String(length=32), nullable=True),
        sa.Column(
            'status',
            sa.Enum('not_visited', 'visited', 'converted', 'not_interested', name='merchantprospectstatus'),
            nullable=False,
        ),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.Column('last_visited_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['branch_id'], ['turbo_branches.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_turbo_merchant_prospects_branch_id'), 'turbo_merchant_prospects', ['branch_id']
    )

    op.create_table(
        'turbo_leads',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('assigned_branch_id', sa.UUID(), nullable=False),
        sa.Column('prospect_id', sa.UUID(), nullable=True),
        sa.Column('source', sa.Enum('o2o_web', 'visit', 'referral', name='leadsource'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('phone', sa.String(length=32), nullable=True),
        sa.Column('occupation', sa.String(length=100), nullable=True),
        sa.Column('age', sa.Integer(), nullable=True),
        sa.Column('quoted_daily_benefit', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('quoted_premium', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column(
            'status', sa.Enum('new', 'contacted', 'converted', 'lost', name='leadstatus'), nullable=False
        ),
        sa.Column('first_response_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['assigned_branch_id'], ['turbo_branches.id']),
        sa.ForeignKeyConstraint(['prospect_id'], ['turbo_merchant_prospects.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_turbo_leads_assigned_branch_id'), 'turbo_leads', ['assigned_branch_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_turbo_leads_assigned_branch_id'), table_name='turbo_leads')
    op.drop_table('turbo_leads')
    sa.Enum(name='leadstatus').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='leadsource').drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f('ix_turbo_merchant_prospects_branch_id'), table_name='turbo_merchant_prospects')
    op.drop_table('turbo_merchant_prospects')
    sa.Enum(name='merchantprospectstatus').drop(op.get_bind(), checkfirst=True)

    op.drop_constraint('fk_users_branch_id', 'users', type_='foreignkey')
    op.drop_index(op.f('ix_users_branch_id'), table_name='users')
    op.drop_column('users', 'branch_id')

    op.drop_table('turbo_branches')

    # Postgres can't drop a single enum value — downgrading past this
    # migration leaves 'branch_champion' in the type, which is harmless
    # (same tradeoff as every other enum-adding migration in this repo).
    op.alter_column('users', 'tenant_id', nullable=False)
