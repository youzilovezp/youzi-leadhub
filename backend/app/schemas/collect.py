"""采集：线索 + 任务 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------- 线索 ----------

# 跟进状态词表（销售跟进生命周期；前端配色：default/info/success/error/warning/success）
FOLLOW_STATUS_OPTIONS: list[dict[str, str]] = [
    {"value": "pending", "label": "待跟进"},
    {"value": "following", "label": "跟进中"},
    {"value": "interested", "label": "有意向"},
    {"value": "not_interested", "label": "无意向"},
    {"value": "unreachable", "label": "联系不上"},
    {"value": "converted", "label": "已成交"},
]
_FOLLOW_STATUS_VALUES = {o["value"] for o in FOLLOW_STATUS_OPTIONS}


class LeadCreate(BaseModel):
    """手工录入。"""

    name: str = Field(min_length=1, max_length=255)
    country: str | None = Field(default=None, max_length=8)
    city: str | None = Field(default=None, max_length=128)
    industry: str | None = Field(default=None, max_length=128)
    address: str | None = Field(default=None, max_length=512)
    phone: str | None = Field(default=None, max_length=64)
    website: str | None = Field(default=None, max_length=512)
    email: str | None = Field(default=None, max_length=255)
    note_source: str = "manual"


class LeadOut(BaseModel):
    id: int
    name: str
    country: str | None
    city: str | None
    industry: str | None
    address: str | None
    phone_raw: str | None
    phone_e164: str | None
    website: str | None
    domain: str | None
    email: str | None
    social: dict[str, Any]
    whatsapp_hit: bool
    whatsapp_url: str | None
    whatsapp_job: bool
    job_urls: list[str]
    enriched_at: datetime | None
    score: int
    score_signals: dict[str, int]
    sources: list[dict[str, Any]]
    owner_id: int | None
    owner_name: str | None = None  # 跟进人姓名（列表接口批量注入；直接 model_validate 时缺省）
    follow_status: str | None
    last_followed_at: datetime | None
    next_follow_at: datetime | None
    is_cn: bool = False  # 中国出海企业特征
    fb_whatsapp: bool = False  # FB 主页带 wa.me（CTWA/私域运营证据）
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LeadCheckWhatsAppRequest(BaseModel):
    """线索列表勾选 → 创建隐式 website_enrich 任务。"""

    lead_ids: list[int] = Field(min_length=1, max_length=1000)


# ---------- 跟进 ----------


class FollowUpCreate(BaseModel):
    """跟进弹窗提交：状态必选，跟进人缺省当前用户。"""

    status: str = Field(min_length=1, max_length=16)
    owner_id: int | None = None
    note: str | None = Field(default=None, max_length=2000)
    next_follow_at: datetime | None = None

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str) -> str:
        if v not in _FOLLOW_STATUS_VALUES:
            allowed = "/".join(sorted(_FOLLOW_STATUS_VALUES))
            raise ValueError(f"非法跟进状态：{v}（可选：{allowed}）")
        return v


class FollowUpOut(BaseModel):
    id: int
    lead_id: int
    user_id: int | None
    user_name: str | None = None  # 跟进操作人姓名（接口批量注入；直接 model_validate 时缺省）
    status: str
    note: str | None
    next_follow_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------- 任务 ----------


def _check_cron(v: str | None) -> str | None:
    if v is None or not v.strip():
        return None
    from apscheduler.triggers.cron import CronTrigger

    try:
        CronTrigger.from_crontab(v.strip())
    except ValueError as exc:
        raise ValueError(f"cron 表达式不合法：{exc}") from exc
    return v.strip()


class TaskCreate(BaseModel):
    collector: str = Field(min_length=1, max_length=32)
    name: str | None = Field(default=None, max_length=128)
    params: dict[str, Any] = Field(default_factory=dict)
    cron_expr: str | None = Field(default=None, max_length=64)

    @field_validator("cron_expr")
    @classmethod
    def _validate_cron(cls, v: str | None) -> str | None:
        return _check_cron(v)


class TaskUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    cron_expr: str | None = None
    enabled: bool | None = None
    params: dict[str, Any] | None = None

    @field_validator("cron_expr")
    @classmethod
    def _validate_cron(cls, v: str | None) -> str | None:
        return _check_cron(v)


class TaskOut(BaseModel):
    id: int
    name: str
    collector: str
    params: dict[str, Any]
    cron_expr: str | None
    enabled: bool
    is_implicit: bool
    created_by: int | None
    created_by_name: str | None = None  # 操作人姓名（列表接口批量注入；直接 model_validate 时缺省）
    status: str
    progress_total: int
    progress_done: int
    leads_added: int
    leads_merged: int
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskLogOut(BaseModel):
    id: int
    task_id: int
    level: str
    message: str
    created_at: datetime

    model_config = {"from_attributes": True}


class CollectorInfo(BaseModel):
    name: str
    title: str
    params: list[dict[str, Any]]
