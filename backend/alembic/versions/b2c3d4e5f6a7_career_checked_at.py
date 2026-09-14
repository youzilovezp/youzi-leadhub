"""career_site 巡检冷却时间（2026-09-11）

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-11

新字段 leads.career_checked_at（NULL=未巡检）——career_site 跑过后写入。
SQL: WHERE career_checked_at IS NULL OR career_checked_at < now() - 7 days
索引：让 career_site 跑分 where 走索引而非全表扫。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b2c3d4e5f6a7'
down_revision: str | None = 'a1b2c3d4e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'leads',
        sa.Column('career_checked_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        'ix_leads_career_checked_at',
        'leads',
        ['career_checked_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_leads_career_checked_at', table_name='leads')
    op.drop_column('leads', 'career_checked_at')
