"""基线迁移：users / roles 初始表结构（由脚手架预生成）。

首次启动时 init_db 会 create_all + stamp 到本基线；
之后 add_module 的增量迁移正常叠加在它之上。
不要修改本文件——表结构变更请生成新的增量迁移。

Revision ID: 0001_baseline
Revises:
Create Date: 2026-01-01
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 基线表的 DDL 由 init_db 的 create_all 直接创建（幂等、跨 SQLite/PG 一致），
    # 本迁移只作为版本链锚点，不重复执行 DDL。
    pass


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TABLE IF EXISTS roles")
