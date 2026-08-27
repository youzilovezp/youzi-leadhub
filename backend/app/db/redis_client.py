"""
异步 Redis 客户端封装。

使用示例：
    from app.db.redis_client import redis_client

    await redis_client.set("foo", "bar", ex=60)
    value = await redis_client.get("foo")
"""

import asyncio

import redis.asyncio as redis

from app.core.config import settings


class RedisClient:
    """封装 redis.asyncio，提供全局单例风格的接口。

    重要：Redis 连接采用**惰性初始化**——第一次调用业务方法时才尝试连接。
    原因：启动期主动 connect() 即便在 catch 里 swallow 异常，
    redis.from_url 内部的 connection_pool 仍会持有 asyncio 引用，
    导致 lifespan 阶段不能正常 yield（uvicorn 持续返回 502）。

    业务方法（get/set/setex/exists/delete/expire/ping）通过 _ensure_connected()
    实现懒加载——单测环境 ASGITransport 不触发 lifespan 时也能用。
    """

    def __init__(self) -> None:
        self._client: redis.Redis | None = None
        self._connect_lock: asyncio.Lock = asyncio.Lock()

    async def _ensure_client(self) -> redis.Redis:
        if self._client is not None:
            return self._client
        async with self._connect_lock:
            if self._client is not None:
                return self._client
            client = redis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
            )
            await client.ping()
            self._client = client
        return self._client

    async def _ensure_connected(self) -> redis.Redis:
        """懒连接：业务方法调用时若未初始化则自动 connect。

        解决 ASGITransport / 单测环境不触发 lifespan → connect() 永远不调用 →
        业务方法全失败的问题。
        """
        if self._client is None:
            await self._ensure_client()
        return self._client  # type: ignore[return-value]

    async def connect(self) -> None:
        """兼容旧 API：尝试预热连接。失败仅记录警告，不阻塞应用。"""
        if self._client is not None:
            return
        try:
            await self._ensure_client()
        except Exception as exc:  # noqa: BLE001
            from loguru import logger

            # 日志只打 host:port，不打 REDIS_URL（含明文密码，会进日志文件）
            logger.warning(
                f"⚠️ Redis 暂时不可达 ({settings.REDIS_HOST}:{settings.REDIS_PORT}): {exc}；"
                "请检查 .env 的 REDIS_PASSWORD 是否与本机 Redis 实际密码一致（本机无密码则留空）；"
                "应用继续启动，业务调用时会感知"
            )

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()  # type: ignore[attr-defined]
            except Exception:
                pass
            self._client = None

    def reset(self) -> None:
        """同步重置（不做 close）：测试间切 event loop 时强制下次 _ensure_client 重连。

        重要：不能用 async close——close 内部 await 走的是旧 loop，新 loop 调用会抛
        'attached to a different loop'。直接清 _client 引用即可，下次业务调用会重连。
        _connect_lock 同样绑定旧 loop（3.10+ Lock 惰性绑定），必须一并重建。
        """
        self._client = None
        self._connect_lock = asyncio.Lock()

    @property
    def client(self) -> redis.Redis:
        """直接访问内部 client（不自动 connect）。测试用，业务请走下方的快捷方法。"""
        if self._client is None:
            raise RuntimeError("Redis 客户端未初始化，请先调用 connect()")
        return self._client

    # ---------- 常用快捷方法（懒连接）----------
    async def ping(self) -> bool:
        """K8s readiness 探针用：检查 Redis 是否在线。"""
        c = await self._ensure_connected()
        result = await c.ping()
        return bool(result)

    async def get(self, key: str) -> str | None:
        c = await self._ensure_connected()
        return await c.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        c = await self._ensure_connected()
        await c.set(key, value, ex=ex)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        c = await self._ensure_connected()
        await c.setex(key, ttl, value)

    async def delete(self, *keys: str) -> int:
        c = await self._ensure_connected()
        return await c.delete(*keys)

    async def exists(self, key: str) -> bool:
        """检查 key 是否存在（包装 await，避免调用方 await bool）。"""
        c = await self._ensure_connected()
        result = await c.exists(key)
        return bool(result)

    async def expire(self, key: str, seconds: int) -> bool:
        c = await self._ensure_connected()
        result = await c.expire(key, seconds)
        return bool(result)


redis_client = RedisClient()
