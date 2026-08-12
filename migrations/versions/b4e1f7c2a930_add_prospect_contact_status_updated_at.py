"""add prospect contact_status_updated_at

Revision ID: b4e1f7c2a930
Revises: d3f8a1c9b6e2
Create Date: 2026-08-12 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4e1f7c2a930'
down_revision: Union[str, None] = 'd3f8a1c9b6e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'turbo_merchant_prospects', sa.Column('contact_status_updated_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('turbo_merchant_prospects', 'contact_status_updated_at')
