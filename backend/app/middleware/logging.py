"""
请求日志中间件：记录 method/path/status/duration。
"""

import time

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration = (time.perf_counter() - start) * 1000
        logger.info(
            f"{request.client.host if request.client else '-'} "
            f"{request.method} {request.url.path} -> {response.status_code} ({duration:.1f}ms)"
        )
        response.headers["X-Process-Time"] = f"{duration:.1f}"
        return response
