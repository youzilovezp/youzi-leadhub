"""
统一异常体系与全局异常处理。
"""

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import ValidationError as PydanticValidationError


class BusinessError(Exception):
    """业务异常基类。使用方式：raise BusinessError(code=40001, message="用户名已存在")"""

    def __init__(self, code: int = 40000, message: str = "业务异常", data=None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


class NotFoundError(BusinessError):
    """404 · 资源不存在"""

    http_status = 404

    def __init__(self, message: str = "资源不存在"):
        super().__init__(code=40400, message=message)


class AuthError(BusinessError):
    """401 · 认证失败（token 缺失 / 无效 / 过期 / 已撤销）"""

    http_status = 401

    def __init__(self, message: str = "认证失败"):
        super().__init__(code=40100, message=message)


class PermissionDeniedError(BusinessError):
    """403 · 权限不足"""

    http_status = 403

    def __init__(self, message: str = "权限不足"):
        super().__init__(code=40300, message=message)


def _wrap(code: int, message: str, data=None) -> dict:
    return {"code": code, "message": message, "data": data}


def _safe_validation_errors(errors) -> list:
    """清洗 pydantic 错误信息，去掉泄露字段：model 类名 / 内部上下文。"""
    safe = []
    for e in errors:
        # e.g. {"type":"string_too_short","loc":("body","username"),"msg":"...","input":"x",...}
        loc = e.get("loc", ())
        # 取最后一个 loc（具体字段名），跳过 "body" / "query" 等中间层
        field = loc[-1] if loc and isinstance(loc, tuple) else None
        if field is None:
            field = str(loc)
        safe.append(
            {
                "field": str(field),
                "message": e.get("msg", ""),
                "type": e.get("type", ""),
            }
        )
    return safe


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def _nf(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404, content=_wrap(exc.code, exc.message, exc.data)
        )

    @app.exception_handler(AuthError)
    async def _au(_: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(
            status_code=401, content=_wrap(exc.code, exc.message, exc.data)
        )

    @app.exception_handler(PermissionDeniedError)
    async def _pd(_: Request, exc: PermissionDeniedError) -> JSONResponse:
        return JSONResponse(
            status_code=403, content=_wrap(exc.code, exc.message, exc.data)
        )

    @app.exception_handler(BusinessError)
    async def _biz(_: Request, exc: BusinessError) -> JSONResponse:
        # 默认 400；子类（Auth/Permission/NotFound）覆盖
        status = getattr(exc, "http_status", 400)
        return JSONResponse(
            status_code=status, content=_wrap(exc.code, exc.message, exc.data)
        )

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI 自动 body 解析失败：可能泄露 model 类名
        return JSONResponse(
            status_code=422,
            content=_wrap(42200, "参数校验失败", _safe_validation_errors(exc.errors())),
        )

    @app.exception_handler(PydanticValidationError)
    async def _pyd(_: Request, exc: PydanticValidationError) -> JSONResponse:
        # 手动 Pydantic 校验（Content-Type 分发的 LoginJSON 等）抛出的异常
        return JSONResponse(
            status_code=422,
            content=_wrap(42200, "参数校验失败", _safe_validation_errors(exc.errors())),
        )

    @app.exception_handler(Exception)
    async def _ex(_: Request, exc: Exception) -> JSONResponse:
        # 服务端记完整日志；客户端只看到通用消息 + trace_id（不泄露 SQL / 栈）
        trace_id = uuid.uuid4().hex[:12]
        logger.exception(
            "unhandled exception trace_id={} exc_type={}", trace_id, type(exc).__name__
        )
        return JSONResponse(
            status_code=500,
            content=_wrap(
                50000,
                f"服务器内部错误（trace_id={trace_id}，请将此 ID 提供给技术支持）",
            ),
        )

    # Pyright 看不到 @app.exception_handler 注册的引用；显式存一下避开误报
    _ = (_nf, _au, _pd, _biz, _val, _pyd, _ex)
