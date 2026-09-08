"""merge agent and leads_domain_unique heads

Revision ID: merge_9c1d_f7c2_a1b2
Revises: 9c1d2e3f4a5b, f7c2a91d4e21
Create Date: 2026-09-07
"""

from collections.abc import Sequence
from typing import Union

# revision identifiers, used by Alembic.
revision: str = 'merge_9c1d_f7c2_a1b2'
down_revision: Union[str, tuple[str, ...], None] = ('9c1d2e3f4a5b', 'f7c2a91d4e21')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
