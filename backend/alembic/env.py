"""Alembic 迁移环境配置。"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

# 导入整个 models 包（自动触发 __init__.py 中的 import 链）
# 比显式列举 User, Role 更鲁棒——add_module 新增模型后只需更新 __init__.py 即可
import app.models  # noqa: F401  # type: ignore[unused-import]  # side-effect import
from alembic import context
from app.core.config import settings
from app.db.base_class import Base

_ = app.models  # 防止 pyright 静态分析看不到 import 副作用

config = context.config
# 用 settings.ALEMBIC_DATABASE_URL（psycopg2 同步驱动），
# 避免与异步 engine 共享连接时触发 greenlet 错误。
# 让手动 alembic upgrade head 与 init_db.py 走同一条 URL，行为一致。
config.set_main_option("sqlalchemy.url", settings.ALEMBIC_DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
