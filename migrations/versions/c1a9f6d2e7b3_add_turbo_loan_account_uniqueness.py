"""add turbo loan account uniqueness constraints

Revision ID: c1a9f6d2e7b3
Revises: b7d4f2a91c3e
Create Date: 2026-08-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1a9f6d2e7b3'
down_revision: Union[str, None] = 'b7d4f2a91c3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # One account per application — the DB-level backstop against the
    # disburse() double-disbursement race (app/services/turbo/loan_service.py),
    # complementing the .with_for_update() row lock added there.
    op.create_unique_constraint(
        'uq_turbo_loan_accounts_application_id', 'turbo_loan_accounts', ['application_id']
    )
    # At most one *active* account per tenant, enforced at the DB level too —
    # closes the race where two different applications for the same tenant
    # are disbursed concurrently (the application-row lock alone only
    # serializes disbursement of the *same* application, not two different
    # ones racing each other).
    op.create_index(
        'uq_turbo_loan_accounts_tenant_active',
        'turbo_loan_accounts',
        ['tenant_id'],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index('uq_turbo_loan_accounts_tenant_active', table_name='turbo_loan_accounts')
    op.drop_constraint('uq_turbo_loan_accounts_application_id', 'turbo_loan_accounts', type_='unique')
