"""leads.domain 部分唯一索引（去重不变量下沉到 DB）

2026-08-31 审计：去重正确性此前全靠代码路径（upsert 同域必同 dedupe_key、
官网发现链 _domain_taken check-then-act），domain 列无 DB 约束——并发窗口
下仍可能双写同域（两条永远无人合并的重复线索）。

本迁移把不变量下沉到数据库：
1. 存量清洗：同 domain 保留最早一行（与 upsert 合并目标 `ORDER BY id` 的
   口径一致），其余行的 domain 置 NULL（这些行本就是漏合并的孤儿，置空后
   后续同域 draft 会正确并入保留行；其 dedupe_key 不受影响）
2. 建 `uq_leads_domain` 部分唯一索引（domain IS NOT NULL，PG/SQLite 3.8+）

downgrade 无法恢复被置空的 domain（信息已在升级时丢弃），只删索引。

Revision ID: f7c2a91d4e21
Revises: e3b8c61a7d40
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f7c2a91d4e21'
down_revision: str | None = 'e3b8c61a7d40'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 存量重复域清洗：每组保留 MIN(id)，其余置 NULL
    op.execute(
        """
        UPDATE leads SET domain = NULL
        WHERE domain IS NOT NULL
          AND id NOT IN (
              SELECT MIN(id) FROM leads WHERE domain IS NOT NULL GROUP BY domain
          )
        """
    )
    op.create_index(
        'uq_leads_domain',
        'leads',
        ['domain'],
        unique=True,
        sqlite_where=sa.text('domain IS NOT NULL'),
        postgresql_where=sa.text('domain IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_leads_domain', table_name='leads')
