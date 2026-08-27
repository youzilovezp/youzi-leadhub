"""采集：线索 + 任务 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------- 线索 ----------


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
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LeadCheckWhatsAppRequest(BaseModel):
    """线索列表勾选 → 创建隐式 website_enrich 任务。"""

    lead_ids: list[int] = Field(min_length=1, max_length=1000)


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
