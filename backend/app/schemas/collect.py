"""采集：线索 + 任务 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------- 线索 ----------

# 跟进状态词表（PRD §23 十态销售生命周期；NULL 与 unassigned 同义=共享池未分配）
# 2026-08-30 由 6 态迁移：following→contacted、interested→opportunity、
# not_interested/unreachable→invalid、converted→won（见迁移 b20022d59541 后续版本）
FOLLOW_STATUS_OPTIONS: list[dict[str, str]] = [
    {"value": "unassigned", "label": "未分配"},
    {"value": "pending", "label": "待跟进"},
    {"value": "contacted", "label": "已联系"},
    {"value": "replied", "label": "已回复"},
    {"value": "opportunity", "label": "有效商机"},
    {"value": "quote", "label": "报价"},
    {"value": "negotiation", "label": "谈判"},
    {"value": "won", "label": "成交"},
    {"value": "invalid", "label": "无效"},
    {"value": "paused", "label": "暂不考虑"},
]
_FOLLOW_STATUS_VALUES = {o["value"] for o in FOLLOW_STATUS_OPTIONS}

# 漏斗阶段顺序（统计口径：从池子到成交）
FUNNEL_STAGES: list[str] = [
    "unassigned", "pending", "contacted", "replied", "opportunity", "quote", "negotiation", "won",
]


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
    score_signals: dict[str, int]  # 六维分 {维度键: 维度分}
    grade: str = "C"  # S/A/B/C
    scenes: list[str] = []  # WhatsApp 场景（customer_service/marketing/transactional/saas）
    saas_signals: dict[str, int] = {}  # SaaS 需求信号 {键: 命中关键词数}
    # 页面出现的全部 WhatsApp 号码（多分线 = 规模化证据，§4.1）
    whatsapp_numbers: list[str] = []
    sources: list[dict[str, Any]]
    owner_id: int | None
    owner_name: str | None = None  # 跟进人姓名（列表接口批量注入；直接 model_validate 时缺省）
    follow_status: str | None
    last_followed_at: datetime | None
    next_follow_at: datetime | None
    is_cn: bool = False  # 中国出海企业特征
    fb_whatsapp: bool = False  # FB 主页带 wa.me（CTWA/私域运营证据）
    target_countries: list[str] = []  # 投放/目标国家（meta_ads 累计，§8）
    export_type: str | None = None  # 出海业务类型
    field_meta: dict[str, Any] = {}  # 字段级数据质量 {字段: {source, updated_at, confidence}}（§32）
    contacts_count: int = 0  # 联系人数（列表接口批量注入）
    recommended_products: list[str] = []  # 推荐产品名（列表接口按行计算，纯函数）
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LeadCheckWhatsAppRequest(BaseModel):
    """线索列表勾选 → 创建隐式 website_enrich 任务。"""

    lead_ids: list[int] = Field(min_length=1, max_length=1000)


# ---------- 联系人 ----------


class ContactCreate(BaseModel):
    """手工新增联系人（seniority 由 job_title 自动分层）。"""

    name: str | None = Field(default=None, max_length=255)
    job_title: str | None = Field(default=None, max_length=128)
    department: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    linkedin: str | None = Field(default=None, max_length=512)
    confidence: int | None = Field(default=None, ge=0, le=100)


class ContactUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    job_title: str | None = Field(default=None, max_length=128)
    department: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    linkedin: str | None = Field(default=None, max_length=512)
    confidence: int | None = Field(default=None, ge=0, le=100)


class ContactOut(BaseModel):
    id: int
    lead_id: int
    name: str | None
    job_title: str | None
    department: str | None
    email: str | None
    phone: str | None
    linkedin: str | None
    seniority: str | None  # tier1/tier2/tier3/unknown/None
    confidence: int
    source: str  # manual / website_enrich
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------- 动态事件 ----------


class LeadEventOut(BaseModel):
    id: int
    lead_id: int
    event_type: str
    payload: dict[str, Any]
    note: str | None
    created_by: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------- 详情 / 推荐 ----------


class RecommendationOut(BaseModel):
    key: str
    name: str
    reason: str
    priority: int


# ---------- 导出 ----------

# 导出字段目录（key, 表头）。前后端口径一致：前端硬编码同名列，后端校验。
EXPORT_FIELDS: list[tuple[str, str]] = [
    ("id", "ID"),
    ("name", "企业名称"),
    ("country", "国家"),
    ("city", "城市"),
    ("industry", "行业"),
    ("website", "官网"),
    ("domain", "域名"),
    ("phone_e164", "电话(E.164)"),
    ("phone_raw", "电话(原始)"),
    ("email", "邮箱"),
    ("grade", "等级"),
    ("score", "Lead Score"),
    ("dim_overseas", "出海指数"),
    ("dim_whatsapp", "WhatsApp指数"),
    ("dim_saas", "SaaS需求"),
    ("dim_scale", "企业规模"),
    ("dim_marketing", "营销活跃"),
    ("dim_contact", "联系人质量"),
    ("whatsapp_hit", "WhatsApp"),
    ("whatsapp_url", "WhatsApp链接"),
    ("whatsapp_numbers", "WhatsApp号码"),
    ("whatsapp_job", "在招WA岗位"),
    ("scenes", "WhatsApp场景"),
    ("saas_signals", "SaaS需求信号"),
    ("is_cn", "中国出海"),
    ("fb_whatsapp", "FB私域"),
    ("job_urls", "在招岗位链接"),
    ("sources", "来源"),
    ("contacts_count", "联系人数"),
    ("contacts_summary", "联系人明细"),
    ("social", "社媒"),
    ("recommended_products", "推荐产品"),
    ("need_types", "需求类型"),
    ("follow_status", "跟进状态"),
    ("owner_name", "跟进人"),
    ("created_at", "创建时间"),
]
EXPORT_FIELD_KEYS = {k for k, _ in EXPORT_FIELDS}


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


class LeadDetailOut(LeadOut):
    """企业画像详情：列表字段 + 六维分 + 联系人 + 事件 + 跟进 + 推荐 + 销售建议 + 商机。"""

    dimensions: dict[str, int] = {}  # 六维分（score_signals 的规范化形态）
    dimension_weights: dict[str, int] = {}
    contacts: list[ContactOut] = []
    events: list[LeadEventOut] = []  # 最近 50 条
    follow_ups: list[FollowUpOut] = []  # 最近 50 条
    recommendations: list[RecommendationOut] = []
    sales_suggestion: str = ""
    opportunities: list[OpportunityOut] = []
    # 需求类型 A-E（补充需求 §4.4）：[{type, label, selling}]
    need_types: list[dict[str, str]] = []


# ---------- 分配（PRD §24） ----------


class AssignPayload(BaseModel):
    """手动分配/转移跟进人。"""

    owner_id: int = Field(ge=1)


class AutoAssignPayload(BaseModel):
    """自动分配：把共享池线索按负载轮转分给候选销售。"""

    owner_ids: list[int] = Field(min_length=1, max_length=50)
    max_per_owner: int = Field(default=50, ge=1, le=500)
    grade: str | None = Field(default=None, pattern="^[SABC]$")
    min_score: int | None = Field(default=None, ge=0)
    industry: str | None = Field(default=None, max_length=128)
    country: str | None = Field(default=None, max_length=8)
    limit: int = Field(default=100, ge=1, le=1000)


# ---------- 商机（PRD §37） ----------


class OpportunityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    amount: int = Field(default=0, ge=0)  # 金额（元）
    expected_close_at: datetime | None = None
    note: str | None = Field(default=None, max_length=2000)


class OpportunityUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    amount: int | None = Field(default=None, ge=0)
    stage: str | None = Field(default=None, max_length=16)  # opportunity/quote/negotiation/won/lost
    expected_close_at: datetime | None = None
    owner_id: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=2000)


class OpportunityOut(BaseModel):
    id: int
    lead_id: int
    name: str
    amount: int
    stage: str
    expected_close_at: datetime | None
    won_at: datetime | None
    owner_id: int | None
    owner_name: str | None = None
    note: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------- 话术审核队列（PRD §56） ----------


class MessageOut(BaseModel):
    id: int
    lead_id: int
    lead_name: str | None = None  # 列表接口注入
    channel: str
    content: str
    status: str  # draft/approved/sent/rejected
    generated_by: str  # llm / template
    created_by: int | None
    reviewed_by: int | None
    sent_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageReviewPayload(BaseModel):
    action: str = Field(pattern="^(approve|reject|mark_sent)$")


# ---------- AI / 自然语言搜索（PRD §25/§26/§27） ----------


class AiAnalysisOut(BaseModel):
    summary: str
    whatsapp_opportunity: str
    pain_points: list[str] = []
    products: list[dict[str, Any]] = []
    entry_point: str
    generated_by: str = "template"  # llm / template（前端明示来源）


class ScriptOut(BaseModel):
    script: str
    generated_by: str = "template"


class NlSearchRequest(BaseModel):
    text: str = Field(min_length=2, max_length=500)


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
