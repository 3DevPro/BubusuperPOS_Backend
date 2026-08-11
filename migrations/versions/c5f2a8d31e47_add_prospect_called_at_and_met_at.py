"""add prospect called_at and met_at

Revision ID: c5f2a8d31e47
Revises: b4e1f7c2a930
Create Date: 2026-08-12 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5f2a8d31e47'
down_revision: Union[str, None] = 'b4e1f7c2a930'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('turbo_merchant_prospects', sa.Column('called_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('turbo_merchant_prospects', sa.Column('met_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('turbo_merchant_prospects', 'met_at')
    op.drop_column('turbo_merchant_prospects', 'called_at')
