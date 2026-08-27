"""
限流（slowapi）。

- 生产启用 Redis 时用 REDIS_URL 作 storage（多 worker 共享限流计数 + 进程重启不丢）
- 兜底 memory://（单进程 dev/无 redis 场景）
- slowapi 缺失时装饰器 no-op，不报错
"""

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    def _build_storage_uri() -> str:
        """优先 Redis，缺失或未启用则 memory。

        注意：REDIS_HOST 留空 = 未启用 Redis（REDIS_ENABLED=False），
        此时不能直接用 REDIS_URL（会是 redis://:6379/0 这种非法 URI，
        slowapi 初始化 storage 时才炸）。必须先看 REDIS_ENABLED。
        """
        try:
            from app.core.config import settings

            if getattr(settings, "REDIS_ENABLED", False) and settings.REDIS_URL:
                return settings.REDIS_URL
        except Exception:
            pass
        return "memory://"

    limiter = Limiter(
        key_func=get_remote_address,
        storage_uri=_build_storage_uri(),
    )

    def _reset_limiter() -> None:
        """测试用：清空限流计数（memory storage 才有 .reset，redis 走 redis.flushdb）。"""
        storage = getattr(limiter, "_storage", None)
        if storage is None:
            return
        try:
            # slowapi MemoryStorage.reset() / RedisStorage.reset() 都会清空计数
            if hasattr(storage, "reset"):
                storage.reset()
        except Exception:
            pass

    # 给 limiter 绑一个 reset 入口（不影响 slowapi 自身 API）
    limiter.reset = _reset_limiter  # type: ignore[attr-defined]
except ImportError:

    class _NoopLimiter:
        def limit(self, *_args, **_kwargs):
            def deco(f):
                return f

            return deco

        def reset(self) -> None:
            pass

    limiter = _NoopLimiter()
