"""采集器框架：LeadDraft / TaskContext / Collector 基类。

新增采集器三步：
    1. 在 collectors/ 下写类继承 Collector（实现 run()）
    2. 需要 dedupe/评分自动复用：产出 LeadDraft 并 ctx.emit(draft)；
       富化型采集器（改存量线索）直接在 run() 里改库
    3. collectors/__init__.py 的 _REGISTRY 注册
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.exceptions import BusinessError


@dataclass
class LeadDraft:
    """采集器产出的原始线索（未归一化、未去重）。"""

    source: str  # 来源标识：google_maps / website_enrich / job_posting / manual
    name: str
    country: str | None = None  # ISO2
    city: str | None = None
    industry: str | None = None
    address: str | None = None
    phone_raw: str | None = None
    website: str | None = None
    email: str | None = None
    social: dict[str, str] = field(default_factory=dict)
    whatsapp_url: str | None = None  # 检测到的 wa.me / 插件链接
    whatsapp_job: bool = False  # 采集器可直接断言「在招 WhatsApp 岗位」
    job_urls: list[str] = field(default_factory=list)


@dataclass
class TaskContext:
    """一次任务执行的上下文：参数、进度、日志、取消、线索落库。"""

    task_id: int
    params: dict[str, Any]
    emit: Callable[[LeadDraft], Awaitable[tuple[int, bool]]]  # 落库，返回 (lead_id, 是否新建)
    log: Callable[[str, str], Awaitable[None]]  # (level, message)
    set_total: Callable[[int], None]
    inc_progress: Callable[[int], None]  # 增量
    _cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    def check_cancelled(self) -> None:
        """采集循环里每个条目调用一次；被取消抛 CancelledError 走统一收尾。"""
        if self._cancel_event.is_set():
            raise asyncio.CancelledError()

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)


class Collector(ABC):
    """采集器基类。"""

    name: str = ""  # 注册键（任务表 collector 字段存的值）
    title: str = ""  # 前端展示名
    # 参数说明（前端动态渲染创建表单用）：[{key, label, required, placeholder, default}]
    param_schema: list[dict[str, Any]] = []

    def validate_params(self, params: dict[str, Any]) -> None:  # noqa: B027  可选钩子，默认不校验
        """创建任务时校验参数，非法直接 BusinessError。默认不校验。"""

    @abstractmethod
    async def run(self, ctx: TaskContext) -> None:
        """执行采集。异常向上抛 → 任务 failed；CancelledError → cancelled。"""


def require_params(params: dict[str, Any], *keys: str, collector: str) -> None:
    """参数必填校验的公共实现。"""
    for key in keys:
        if not str(params.get(key) or "").strip():
            raise BusinessError(code=40001, message=f"{collector} 采集器缺少必填参数：{key}")


def split_csv(value: str | None) -> list[str]:
    """逗号分隔参数 → 去空白的列表。"""
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]
