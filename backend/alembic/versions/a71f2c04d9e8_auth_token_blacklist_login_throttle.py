"""auth: token_blacklist + login_throttles（登出即失效 / 暴力破解防护）

Revision ID: a71f2c04d9e8
Revises: 6175a9ca77ce
Create Date: 2026-08-30 21:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a71f2c04d9e8'
down_revision: str | None = '6175a9ca77ce'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 已撤销 JWT：jti 唯一索引点查，行按 token exp 过期并由写入方清理
    op.create_table(
        'token_blacklist',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('jti', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_token_blacklist_jti'), 'token_blacklist', ['jti'], unique=True)
    op.create_index(op.f('ix_token_blacklist_user_id'), 'token_blacklist', ['user_id'], unique=False)
    op.create_index(op.f('ix_token_blacklist_expires_at'), 'token_blacklist', ['expires_at'], unique=False)

    # 登录失败计数与锁定（u:<username>|ip:<ip> 与 ip:<ip> 两类键）
    op.create_table(
        'login_throttles',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('throttle_key', sa.String(length=191), nullable=False),
        sa.Column('fail_count', sa.Integer(), nullable=False),
        sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_login_throttles_throttle_key'), 'login_throttles', ['throttle_key'], unique=True)

    # 角色权限码补齐：ROLE_SEEDS 口径（旧库 admin 等角色可能无 permissions）
    _seed_role_permissions()


def _seed_role_permissions() -> None:
    """与 app/models/role.py ROLE_SEEDS 同口径（迁移内自带副本，避免 import 应用代码）。"""
    import json

    conn = op.get_bind()
    roles = [
        ("admin", "管理员", ["lead:read", "lead:write", "lead:delete", "assign:lead", "task:manage", "user:manage", "stats:read"]),
        ("sales_manager", "销售主管", ["lead:read", "lead:write", "assign:lead", "stats:read"]),
        ("sales", "销售", ["lead:read", "lead:write"]),
        ("operator", "运营", ["lead:read", "task:manage", "stats:read"]),
        ("data_admin", "数据管理员", ["lead:read", "stats:read"]),
    ]
    is_pg = conn.dialect.name == "postgresql"
    cast = "CAST(:perms AS JSON)" if is_pg else ":perms"
    for code, name, perms in roles:
        existing = conn.execute(
            sa.text("SELECT id FROM roles WHERE code = :code"), {"code": code}
        ).scalar_one_or_none()
        if existing is None:
            conn.execute(
                sa.text(
                    "INSERT INTO roles (name, code, permissions, remark, created_at, updated_at) "
                    f"VALUES (:name, :code, {cast}, '系统内置', now(), now())"
                ),
                {"name": name, "code": code, "perms": json.dumps(perms)},
            )
        else:
            conn.execute(
                sa.text(f"UPDATE roles SET permissions = {cast} WHERE code = :code"),
                {"code": code, "perms": json.dumps(perms)},
            )


def downgrade() -> None:
    op.drop_index(op.f('ix_login_throttles_throttle_key'), table_name='login_throttles')
    op.drop_table('login_throttles')
    op.drop_index(op.f('ix_token_blacklist_expires_at'), table_name='token_blacklist')
    op.drop_index(op.f('ix_token_blacklist_user_id'), table_name='token_blacklist')
    op.drop_index(op.f('ix_token_blacklist_jti'), table_name='token_blacklist')
    op.drop_table('token_blacklist')
