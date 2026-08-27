"""
Leadhub - FastAPI 应用入口。

启动方式：
    开发：uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
    生产：gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker

健康检查：
    /healthz  - 进程级（K8s liveness）
    /readyz   - 含 DB ping（K8s readiness）
"""
from contextlib import asynccontextmanager

import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging
from app.core.ratelimit import limiter
from app.db.init_db import init_db
from app.db.session import async_session
from app.middleware.logging import LoggingMiddleware

try:
    from slowapi.errors import RateLimitExceeded
except ImportError:  # 默认（无 Redis）模式未安装 slowapi——限流走 no-op
    RateLimitExceeded = None  # type: ignore[assignment,misc]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化资源，关闭时释放。"""
    setup_logging()
    # 生产安全检查：默认 admin/admin + 默认密码是**严重的生产隐患**。
    # 这里强警告，让运维在生产部署前必须改。
    if settings.APP_ENV == "prod":
        _check_prod_secrets()
    await init_db()

    # 线索采集：后台任务执行器 + 定时调度（多 worker 部署只在单进程开 SCHEDULER_ENABLED）
    from app.services import scheduler as collect_scheduler
    from app.services.task_runner import task_runner

    await task_runner.start()
    await collect_scheduler.start()

    yield

    await collect_scheduler.stop()
    await task_runner.stop()


def _check_prod_secrets() -> None:
    """生产环境启动时的硬性安全检查：弱密码直接拒绝启动。"""
    weak_passwords = {"admin", "password", "123456", "admin123", "changeme"}
    if settings.INITIAL_ADMIN_PASSWORD in weak_passwords:
        from loguru import logger
        logger.error(
            "🚨 生产环境检测到默认弱密码 INITIAL_ADMIN_PASSWORD={!r}\n"
            "   必须用 --admin-pass 或环境变量指定强密码（≥ 16 位）\n"
            "   启动中止：请修改 .env 或重新跑 init.py --admin-pass",
            settings.INITIAL_ADMIN_PASSWORD,
        )
        raise SystemExit(1)


def create_app() -> FastAPI:
    """应用工厂。"""
    # 生产环境：禁用 /docs /redoc /openapi.json（暴露全部 schema 给匿名用户）
    is_prod = settings.APP_ENV == "prod" and not settings.DEBUG
    app = FastAPI(
        title=settings.APP_NAME,
        version=__version__,
        description=settings.APP_DESCRIPTION,
        openapi_url=None if is_prod else f"{settings.API_V1_PREFIX}/openapi.json",
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        lifespan=lifespan,
    )

    # 中间件顺序：Starlette add_middleware 是 LIFO——最后 add 的最外层。
    # 因此 LoggingMiddleware 必须在最后 add，才能捕获到所有外层（CORS / TrustedHost）的事件。
    if settings.TRUSTED_HOSTS:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.TRUSTED_HOSTS)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )
    # slowapi 限流：必须把 limiter 挂到 app.state，装饰器(@limiter.limit)才能找到
    # 注意：不要同时 add SlowAPIMiddleware——会和 @limiter.limit 装饰器**双重计数**，
    # 5/minute 实际只允许 ~3 次（中间件 +1、装饰器 +1）。只用装饰器 + 全局异常处理。
    app.state.limiter = limiter
    # 最后注册 = 最外层，捕获所有请求/响应
    app.add_middleware(LoggingMiddleware)

    # 安全 headers（防止 clickjacking / XSS 兜底）
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # HSTS 仅在生产启用——开发用 http://localhost 时开启会让浏览器拒绝后续连接
        if is_prod:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp

    register_exception_handlers(app)

    # 限流触发后：429 + 友好消息（不暴露内部堆栈）
    # 仅在 slowapi 可用时注册（无 Redis 模式 limiter 是 no-op，不会触发该异常）
    if RateLimitExceeded is not None:
        @app.exception_handler(RateLimitExceeded)
        async def _rate_limit_handler(_: Request, exc: RateLimitExceeded) -> JSONResponse:
            return JSONResponse(
                status_code=429,
                content={"code": 42900, "message": "请求过于频繁，请稍后重试"},
            )

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    # /healthz: 进程级（K8s liveness）
    @app.get("/healthz", tags=["健康检查"])
    async def healthz() -> dict:
        return {"status": "ok", "version": __version__}

    # /readyz: 含依赖检查（K8s readiness）；失败返回 503
    @app.get("/readyz", tags=["健康检查"])
    async def readyz() -> JSONResponse:
        ok = True
        details: dict = {}
        try:
            async with async_session() as s:
                await s.execute(sa.text("SELECT 1"))
            details["database"] = "ok"
        except Exception:  # noqa: BLE001
            ok = False
            # 不外泄错误类名 / 详情,服务端打日志即可
            details["database"] = "down"

        return JSONResponse(
            status_code=200 if ok else 503,
            content={"status": "ready" if ok else "not_ready", **details},
        )

    return app


app = create_app()
