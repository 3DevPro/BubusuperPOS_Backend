"""add prospect application_interest and contact_status

Revision ID: d3f8a1c9b6e2
Revises: c1a9f6d2e7b3
Create Date: 2026-08-11 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3f8a1c9b6e2'
down_revision: Union[str, None] = 'c1a9f6d2e7b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    application_interest_enum = sa.Enum(
        'not_applied', 'applied_loan', 'applied_insurance', name='prospectapplicationinterest'
    )
    application_interest_enum.create(bind, checkfirst=True)
    op.add_column(
        'turbo_merchant_prospects',
        sa.Column('application_interest', application_interest_enum, nullable=False, server_default='not_applied'),
    )

    contact_status_enum = sa.Enum('not_scheduled', 'called', 'met', 'unreachable', name='prospectcontactstatus')
    contact_status_enum.create(bind, checkfirst=True)
    op.add_column(
        'turbo_merchant_prospects',
        sa.Column('contact_status', contact_status_enum, nullable=False, server_default='not_scheduled'),
    )


def downgrade() -> None:
    op.drop_column('turbo_merchant_prospects', 'contact_status')
    sa.Enum(name='prospectcontactstatus').drop(op.get_bind(), checkfirst=True)

    op.drop_column('turbo_merchant_prospects', 'application_interest')
    sa.Enum(name='prospectapplicationinterest').drop(op.get_bind(), checkfirst=True)
