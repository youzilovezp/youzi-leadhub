"""query indexes: industry / next_follow_at / (score,id) 复合

Revision ID: c3e8a91b6f04
Revises: a71f2c04d9e8
Create Date: 2026-08-30 22:00:00.000000

列表高频筛选（industry 等值 / next_follow_at 到期回访）与默认排序
（score desc, id desc）此前无索引，百万级下全表扫。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3e8a91b6f04'
down_revision: str | None = 'a71f2c04d9e8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(op.f('ix_leads_industry'), 'leads', ['industry'], unique=False)
    op.create_index(op.f('ix_leads_next_follow_at'), 'leads', ['next_follow_at'], unique=False)
    # 默认排序（列表/导出/自动分配共用）：score desc, id desc
    op.create_index(op.f('ix_leads_score_id'), 'leads', ['score', 'id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_leads_score_id'), table_name='leads')
    op.drop_index(op.f('ix_leads_next_follow_at'), table_name='leads')
    op.drop_index(op.f('ix_leads_industry'), table_name='leads')
