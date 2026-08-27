"""测试公共 fixtures。

设计：
- **封闭测试库**：必须在导入任何 app 模块**之前**把 DB_TYPE 切到临时 SQLite——
  这样测试永远不碰开发数据库（.env 的 PostgreSQL），也不需要任何中间件。
  pydantic-settings 在 Settings() 实例化时读环境变量，所以只要抢在第一个
  `import app.*` 之前设置即可。
- function-scoped engine dispose：每个测试结束 dispose 引擎（避免连接
  跨 event loop 导致 'Task got Future attached to a different loop'）
- function-scoped redis reset：每个测试结束 reset redis_client（同样的原因）
- function-scoped client：每次新 event loop + 新 ASGI app lifespan
- 使用 settings 的 admin 凭证（与 init_db 一致）
"""

import os
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

# ---------- 封闭测试库（必须在 import app 之前！）----------
# 环境变量设置插在标准 import 与 `import app.*` 之间：ruff E402 只针对标准库/三方库
# import 混排，这里对 app 导入加 noqa 是刻意为之——顺序本身就是测试隔离机制的一部分。
_TEST_DB = Path(tempfile.mkdtemp(prefix="yz-test-db-")) / "test.db"
os.environ["DB_TYPE"] = "sqlite"
os.environ["SQLITE_PATH"] = str(_TEST_DB)
# Redis：测试一律走内存限流，不依赖真实 Redis（黑名单测试按 REDIS_ENABLED 分支断言）
os.environ["REDIS_HOST"] = ""
# git clone 的项目没有 .env（被 gitignore）——测试也能跑：给一个兜底密钥
os.environ.setdefault(
    "SECRET_KEY", "test-only-secret-key-0123456789abcdef0123456789abcdef"
)

from app.core.config import settings  # noqa: E402  必须在环境变量设置之后
from app.db.init_db import init_db  # noqa: E402


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """每个测试独立 session：测试结束自动 rollback，干净隔离。"""
    from app.db.session import async_session

    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
def admin_credentials() -> dict[str, str]:
    """从 settings 读 admin 凭证——避免硬编码用户名/密码。"""
    return {
        "username": settings.INITIAL_ADMIN_USERNAME,
        "password": settings.INITIAL_ADMIN_PASSWORD,
    }


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """提供 httpx AsyncClient 走 ASGI transport；不依赖真实网络端口。

    关键：
    1. base_url 用 "testserver" 让 Host 头匹配 settings.TRUSTED_HOSTS
    2. 每次 fixture 入口 dispose 旧引擎 + reset redis（防跨 loop 复用）
    3. 触发 init_db 一次（在 ASGI app lifespan 里）
    4. 限流器换成内存存储（避免 reset 时误清 blacklist 用的 redis 库）
    """
    from app.core import ratelimit as _rl_mod
    from app.db.session import engine

    # redis 包只在 --with-redis 模式安装；默认模式没有 redis_client 可用
    try:
        from app.db.redis_client import redis_client
    except ImportError:
        redis_client = None  # type: ignore[assignment]

    # 关键修复：每个 test 自己的 event loop——上一个 test 的连接绑在死 loop 上
    # 必须先 dispose 引擎 + 重置 redis_client
    await engine.dispose()
    if redis_client is not None:
        redis_client.reset()

    # 限流器：测试场景直接禁用（5/min 限流会让 pytest 多 login 测试触发 429）
    # 真实限流逻辑由慢速 e2e / 压测覆盖；单元测试重点是业务逻辑
    _rl_mod.limiter.enabled = False

    from app.main import app

    await init_db()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_after_test() -> AsyncGenerator[None, None]:
    """每个测试结束：dispose 引擎 + 关 redis，避免连接泄漏到下一个 test 的 loop。"""
    yield
    try:
        from app.db.session import engine

        await engine.dispose()
        try:
            from app.db.redis_client import redis_client
        except ImportError:
            pass
        else:
            redis_client.reset()
    except Exception:
        pass


__all__ = [
    "client",
    "admin_credentials",
    "db_session",
]
