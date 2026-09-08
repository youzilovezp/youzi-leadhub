"""为 lead 表增加 qualify_reason JSON 字段（AI 判定理由，方向 B）

Revision ID: a1b2c3d4e5f6
Revises: merge_9c1d_f7c2_a1b2
Create Date: 2026-09-07

字段：
    qualify_reason JSON NULL
        {"summary", "drivers", "blockers", "next_action", "generated_by",
         "generated_at", "cache_key"}
    模板拼接永远可用；LLM 增强可选（缓存键 = hash(score+signals+icp+contacts)）。
    触发点：富化完成 / 评分变更 / 联系人变更 三处 hook。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'a1b2c3d4e5f6'
down_revision: str | None = 'merge_9c1d_f7c2_a1b2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'leads',
        sa.Column('qualify_reason', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('leads', 'qualify_reason')