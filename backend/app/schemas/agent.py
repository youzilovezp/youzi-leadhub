"""代理式销售运营 Pydantic schemas。

按 PRD AGENTS.md「字段校验放 Pydantic schema（min_length/max_length），不要在 endpoint 里手写 if」：
    所有请求体一律走这里。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.agent import (
    FORECAST_STAGES,
    OUTREACH_CHANNELS,
    OUTREACH_STATUSES,
    PROBABILITY_BY_STAGE,
    REPLY_INTENTS,
    REPLY_SENTIMENTS,
)

_OUTREACH_STATUS_SET = set(OUTREACH_STATUSES)
_SENTIMENT_SET = set(REPLY_SENTIMENTS)
_INTENT_SET = set(REPLY_INTENTS)
_STAGE_SET = set(FORECAST_STAGES)


# ---------- 序列 ----------


class OutreachStep(BaseModel):
    day_offset: int = Field(ge=0, le=180)  # 0~180 天（T+0 ~ T+180）
    channel: str
    template_hint: str = Field(default="", max_length=255)

    @field_validator("channel")
    @classmethod
    def _check_channel(cls, v: str) -> str:
        if v not in OUTREACH_CHANNELS:
            raise ValueError(f"非法通道：{v}（可选：{OUTREACH_CHANNELS}）")
        return v


class SequenceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    scenario: str = Field(default="first_touch", max_length=32)
    channel: str = "email"
    steps: list[OutreachStep] = Field(min_length=1, max_length=10)

    @field_validator("channel")
    @classmethod
    def _check_channel(cls, v: str) -> str:
        if v not in OUTREACH_CHANNELS:
            raise ValueError(f"非法通道：{v}")
        return v

    @model_validator(mode="after")
    def _steps_unique_offset(self) -> "SequenceCreate":
        offsets = [s.day_offset for s in self.steps]
        if len(offsets) != len(set(offsets)):
            raise ValueError("步序 day_offset 必须唯一")
        return self


class SequenceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    steps: list[OutreachStep] | None = Field(default=None, min_length=1, max_length=10)
    active: bool | None = None


class SequenceOut(BaseModel):
    id: int
    name: str
    description: str | None
    scenario: str
    channel: str
    steps: list[dict[str, Any]]
    active: bool
    owner_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------- 外联消息 ----------


class OutreachDraftRequest(BaseModel):
    """AI 起草个性化外联（必传 lead + 可选 step/channel/contact_id）。"""

    lead_id: int = Field(ge=1)
    sequence_id: int | None = None
    step_index: int | None = Field(default=None, ge=0)
    channel: str | None = None
    contact_id: int | None = None
    # 上下文：销售可手动补充场景/卖点，覆盖默认模板
    context_hint: str | None = Field(default=None, max_length=1000)

    @field_validator("channel")
    @classmethod
    def _check_channel(cls, v: str | None) -> str | None:
        if v is not None and v not in OUTREACH_CHANNELS:
            raise ValueError(f"非法通道：{v}")
        return v


class OutreachMessageUpdate(BaseModel):
    """编辑草稿（仅 draft 状态可调；approved/sent 等被锁）。"""

    subject: str | None = Field(default=None, max_length=255)
    body: str | None = Field(default=None, max_length=20000)
    scheduled_at: datetime | None = None


class OutreachMessageOut(BaseModel):
    id: int
    lead_id: int
    contact_id: int | None
    sequence_id: int | None
    step_index: int | None
    channel: str
    subject: str | None
    body: str
    status: str
    llm_generated: bool
    generated_by: str
    scheduled_at: datetime | None
    sent_at: datetime | None
    sent_error: str | None
    reply_id: int | None
    owner_id: int | None
    locked_at: datetime | None
    approved_by: int | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OutreachMessageList(BaseModel):
    """列表扩展（带 lead_name/contact_name，列表接口批量注入）。"""

    items: list[dict[str, Any]] = []
    total: int = 0
    page: int = 1
    page_size: int = 20


# ---------- 回复 ----------


class ReplyCreate(BaseModel):
    """人工录入回复（邮件回信自动同步见 /replies/webhook）。"""

    lead_id: int = Field(ge=1)
    message_id: int | None = None
    channel: str = "email"
    from_address: str | None = Field(default=None, max_length=255)
    subject: str | None = Field(default=None, max_length=255)
    body: str = Field(min_length=1, max_length=20000)
    sentiment: str | None = None
    intent: str | None = None

    @field_validator("channel")
    @classmethod
    def _check_channel(cls, v: str) -> str:
        if v not in OUTREACH_CHANNELS:
            raise ValueError(f"非法通道：{v}")
        return v

    @field_validator("sentiment")
    @classmethod
    def _check_sentiment(cls, v: str | None) -> str | None:
        if v is not None and v not in _SENTIMENT_SET:
            raise ValueError(f"非法情感：{v}")
        return v

    @field_validator("intent")
    @classmethod
    def _check_intent(cls, v: str | None) -> str | None:
        if v is not None and v not in _INTENT_SET:
            raise ValueError(f"非法意向：{v}")
        return v


class ReplyLabelOverride(BaseModel):
    """人工覆盖 sentiment/intent（审计：保留 llm_labeled=True 但置 overridden=True）。"""

    sentiment: str | None = None
    intent: str | None = None

    @field_validator("sentiment")
    @classmethod
    def _check_sentiment(cls, v: str | None) -> str | None:
        if v is not None and v not in _SENTIMENT_SET:
            raise ValueError(f"非法情感：{v}")
        return v

    @field_validator("intent")
    @classmethod
    def _check_intent(cls, v: str | None) -> str | None:
        if v is not None and v not in _INTENT_SET:
            raise ValueError(f"非法意向：{v}")
        return v


class ReplyOut(BaseModel):
    id: int
    lead_id: int
    message_id: int | None
    channel: str
    from_address: str | None
    subject: str | None
    body: str
    sentiment: str | None
    intent: str | None
    summary: str | None
    received_at: datetime
    processed_at: datetime | None
    crm_action: str | None
    llm_labeled: bool
    overridden: bool
    handled_by: int | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------- 商机预测 ----------


class ForecastDealCreate(BaseModel):
    lead_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=255)
    stage: str
    amount: float = Field(ge=0)
    probability: int | None = Field(default=None, ge=0, le=100)
    close_date: datetime | None = None
    is_primary: bool = False
    note: str | None = Field(default=None, max_length=2000)
    owner_id: int | None = None

    @field_validator("stage")
    @classmethod
    def _check_stage(cls, v: str) -> str:
        if v not in _STAGE_SET:
            raise ValueError(f"非法阶段：{v}")
        return v


class ForecastDealUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    stage: str | None = None
    amount: float | None = Field(default=None, ge=0)
    probability: int | None = Field(default=None, ge=0, le=100)
    close_date: datetime | None = None
    is_primary: bool | None = None
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("stage")
    @classmethod
    def _check_stage(cls, v: str | None) -> str | None:
        if v is not None and v not in _STAGE_SET:
            raise ValueError(f"非法阶段：{v}")
        return v


class ForecastDealOut(BaseModel):
    id: int
    lead_id: int
    name: str
    stage: str
    amount: float
    probability: int
    close_date: datetime | None
    is_primary: bool
    note: str | None
    owner_id: int | None
    weighted_amount: float = 0  # 派生字段：amount * probability / 100
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ForecastSummaryOut(BaseModel):
    """实时预测汇总（按当前 deal 表算，不依赖快照）。"""

    total_amount: float
    weighted_total: float
    open_weighted: float  # 仅 open stage（不含 won/lost）
    deal_count: int
    open_deal_count: int
    by_stage: dict[str, dict[str, float]]  # {stage: {count, amount, weighted}}
    by_owner: dict[str, dict[str, float]]  # {owner_name: {count, amount, weighted}}


class ForecastSnapshotOut(BaseModel):
    id: int
    period: str
    period_start: datetime
    period_end: datetime
    data: dict[str, Any]
    computed_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------- 同步 CRM（手动） ----------


class SyncCRMRequest(BaseModel):
    """手动触发 reply→CRM 同步（自动跑过的人工补救入口）。"""

    reply_id: int = Field(ge=1)
    target_status: str | None = None  # None = 按 intent 自动判定


# ---------- 排程 ----------


class ScheduleOutreachRequest(BaseModel):
    """对一组线索批量按序列生成草稿并排程。"""

    sequence_id: int = Field(ge=1)
    lead_ids: list[int] = Field(min_length=1, max_length=500)
    start_at: datetime | None = None  # None = 立即
    owner_id: int | None = None  # None = 当前用户


class ScheduleOutreachResult(BaseModel):
    scheduled: int = 0
    skipped: int = 0
    errors: list[str] = []
