"""add loan review pipeline stages

Revision ID: d2e8b4c1f5a7
Revises: c5f2a8d31e47
Create Date: 2026-08-12 12:00:00.000000

Postgres >= 12 (this stack runs postgres:18-alpine — see
BubusuperPOS_Infra/docker-compose.yml) allows ALTER TYPE ... ADD VALUE inside
a transaction block, so no autocommit_block() is needed here (already proven
on this stack by b7d4f2a91c3e). The rule that *does* still apply: a newly
added enum value can't be referenced in the same transaction it was added in.
This migration never hits that — there's no UPDATE ... SET status=<new value>
below, and turbo_loan_application_events.to_status is a plain String(32)
specifically to avoid ever needing one as the pipeline grows more stages.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = 'd2e8b4c1f5a7'
down_revision: Union[str, None] = 'c5f2a8d31e47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE loanapplicationstatus ADD VALUE IF NOT EXISTS 'doc_review'")
    op.execute("ALTER TYPE loanapplicationstatus ADD VALUE IF NOT EXISTS 'collateral_check'")
    op.execute("ALTER TYPE loanapplicationstatus ADD VALUE IF NOT EXISTS 'under_review'")

    op.add_column(
        'turbo_loan_applications',
        sa.Column('collateral_detail', JSONB(), nullable=False, server_default='{}'),
    )
    op.add_column('turbo_loan_applications', sa.Column('rejection_reason', sa.String(length=500), nullable=True))
    op.add_column(
        'turbo_loan_applications',
        sa.Column('stage_started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.add_column('turbo_loan_applications', sa.Column('reviewed_by_user_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_turbo_loan_applications_reviewed_by_user_id',
        'turbo_loan_applications',
        'users',
        ['reviewed_by_user_id'],
        ['id'],
    )
    # Written since b7d4f2a91c3e but never read until now (see the plan doc)
    # — the branch review queue filters on it every poll.
    op.create_index(
        op.f('ix_turbo_loan_applications_assigned_branch_id'), 'turbo_loan_applications', ['assigned_branch_id']
    )

    op.create_table(
        'turbo_loan_application_events',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('application_id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('branch_id', sa.UUID(), nullable=True),
        sa.Column('from_status', sa.String(length=32), nullable=True),
        sa.Column('to_status', sa.String(length=32), nullable=False),
        sa.Column('actor_user_id', sa.UUID(), nullable=True),
        sa.Column('actor_name', sa.String(length=255), nullable=False),
        sa.Column('actor_kind', sa.String(length=16), nullable=False),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['application_id'], ['turbo_loan_applications.id']),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['branch_id'], ['turbo_branches.id']),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_turbo_loan_application_events_application_id'),
        'turbo_loan_application_events',
        ['application_id'],
    )
    op.create_index(
        op.f('ix_turbo_loan_application_events_tenant_id'), 'turbo_loan_application_events', ['tenant_id']
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_turbo_loan_application_events_tenant_id'), table_name='turbo_loan_application_events')
    op.drop_index(
        op.f('ix_turbo_loan_application_events_application_id'), table_name='turbo_loan_application_events'
    )
    op.drop_table('turbo_loan_application_events')

    op.drop_index(op.f('ix_turbo_loan_applications_assigned_branch_id'), table_name='turbo_loan_applications')
    op.drop_constraint(
        'fk_turbo_loan_applications_reviewed_by_user_id', 'turbo_loan_applications', type_='foreignkey'
    )
    op.drop_column('turbo_loan_applications', 'reviewed_by_user_id')
    op.drop_column('turbo_loan_applications', 'stage_started_at')
    op.drop_column('turbo_loan_applications', 'rejection_reason')
    op.drop_column('turbo_loan_applications', 'collateral_detail')

    # Postgres can't drop individual enum values — downgrading past this
    # migration leaves doc_review/collateral_check/under_review in the type,
    # same harmless tradeoff as every other enum-adding migration in this
    # repo (see b7d4f2a91c3e and 2fbbe7ec560d's downgrade comments).
