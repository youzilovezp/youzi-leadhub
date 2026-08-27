"""通用响应 schema。"""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ResponseModel(BaseModel, Generic[T]):
    """统一响应结构。

    所有 API 返回都应使用此格式：
        {"code": 0, "message": "ok", "data": ...}
    """

    code: int = Field(default=0, description="状态码，0 表示成功")
    message: str = Field(default="ok")
    data: T | None = None


class PageParams(BaseModel):
    """分页参数。"""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)


class PageResponse(BaseModel, Generic[T]):
    """分页响应。"""

    items: list[T]
    total: int
    page: int
    page_size: int


class TokenPayload(BaseModel):
    sub: str
    exp: int
