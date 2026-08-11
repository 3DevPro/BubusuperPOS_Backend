"""add applied_both to prospect application interest enum

Revision ID: a7c2e94f1d08
Revises: d3f8a1c9b6e2
Create Date: 2026-08-11 19:10:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a7c2e94f1d08'
down_revision: Union[str, None] = 'd3f8a1c9b6e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE prospectapplicationinterest ADD VALUE IF NOT EXISTS 'applied_both'")


def downgrade() -> None:
    # Postgres can't drop a single enum value — same tradeoff as every other
    # enum-adding migration in this repo (see 2fbbe7ec560d's downgrade).
    pass
