"""
异步数据库引擎与会话工厂。

使用示例：
    from app.db.session import async_session

    async with async_session() as session:
        result = await session.execute(select(User))
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.db.base_class import Base


def _engine_kwargs() -> dict:
    """SQLite 加 busy 超时（默认 5s 并发写易 database is locked）；PG 用连接池参数。"""
    if settings.DB_TYPE == "sqlite":
        return {"connect_args": {"timeout": 15}}
    return {
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
    }


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_pre_ping=settings.DB_POOL_PRE_PING,  # 防长连接被 PG / NAT 静默断开
    pool_recycle=settings.DB_POOL_RECYCLE,  # 1 小时回收连接
    future=True,
    **_engine_kwargs(),
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：每次请求一个独立 Session。"""
    async with async_session() as session:
        yield session


__all__ = ["Base", "engine", "async_session", "get_session"]
