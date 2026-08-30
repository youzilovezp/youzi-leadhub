"""意向评分 V2：六维加权模型（PRD 口径，0-100 封顶）。

Lead Score = 出海×25% + WhatsApp×30% + SaaS需求×20% + 规模×10% + 营销活跃×10% + 联系人×5%
分级：80-100=S（立即跟进） 60-79=A（高潜力） 40-59=B（培育池） 0-39=C（暂不优先）

设计说明：
- 六个维度各自 0-100 封顶，总分 = 加权和四舍五入后 clamp 0-100
- score_signals JSON 存 {维度键: 维度分}（六键），明细可追溯、详情页直读
- 旧的 8 布尔信号加和制（不封顶、最高 110）已废弃；旧信号作为维度输入继续生效
- 规模/营销活跃两维没有直接数据（无员工数/流量），用可观测代理：岗位数/社媒广度/
  来源数/在投广告，口径见各维度规则——是代理不是真值，文档已标注
- 权重可用 .env SCORING_DIM_WEIGHTS 覆盖（JSON，键为六维名），按和归一化
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings

# ---------- 维度定义 ----------

DIM_WEIGHTS: dict[str, int] = {
    "overseas": 25,  # 出海度
    "whatsapp": 30,  # WhatsApp 使用强度
    "saas": 20,  # SaaS 需求度
    "scale": 10,  # 企业规模（代理）
    "marketing": 10,  # 营销活跃度（代理）
    "contact": 5,  # 联系人质量
}

DIM_LABELS_ZH: dict[str, str] = {
    "overseas": "出海指数",
    "whatsapp": "WhatsApp 指数",
    "saas": "SaaS 需求",
    "scale": "企业规模",
    "marketing": "营销活跃",
    "contact": "联系人质量",
}

# SaaS 信号分值表（与 scenes.SAAS_SIGNALS 的键对齐；命中即得满格分）
SAAS_SIGNAL_POINTS: dict[str, int] = {
    "crm": 22,
    "helpdesk": 22,
    "chatbot": 18,
    "ai_service": 18,
    "marketing_automation": 12,
    "omnichannel": 8,
}


def effective_dim_weights() -> dict[str, int]:
    """默认六维权重 + .env SCORING_DIM_WEIGHTS 按键覆盖，按和归一化（总分仍 ≤100）。

    舍入后残差补到权重最大的维度，保证合计恰为 100。
    """
    merged = {**DIM_WEIGHTS, **{k: v for k, v in settings.SCORING_DIM_WEIGHTS.items() if k in DIM_WEIGHTS}}
    total = sum(merged.values())
    if total <= 0:
        return dict(DIM_WEIGHTS)
    normalized = {k: round(w * 100 / total) for k, w in merged.items()}
    drift = 100 - sum(normalized.values())
    if drift:
        top = max(normalized, key=lambda k: normalized[k])
        normalized[top] += drift
    return normalized


def grade_of(score: int) -> str:
    """总分 → S/A/B/C 分级。"""
    if score >= 80:
        return "S"
    if score >= 60:
        return "A"
    if score >= 40:
        return "B"
    return "C"


def score_lead_inputs(
    *,
    is_cn: bool = False,
    fb_whatsapp: bool = False,
    country: str | None = None,
    website: str | None = None,
    whatsapp_hit: bool = False,
    whatsapp_url: str | None = None,
    whatsapp_job: bool = False,
    scenes: list[str] | None = None,
    whatsapp_numbers: list[str] | None = None,
    saas_signals: dict[str, Any] | None = None,
    job_urls: list[str] | None = None,
    social: dict[str, Any] | None = None,
    email: str | None = None,
    phone_raw: str | None = None,
    phone_e164: str | None = None,
    sources: list[dict[str, Any]] | None = None,
    contacts_count: int = 0,
    has_tier1: bool = False,
    has_tier2: bool = False,
) -> tuple[int, dict[str, int], str]:
    """六维评分纯函数：返回 (总分, {维度: 维度分}, 分级)。

    所有输入都是行属性，无 IO；迁移回填与 ORM 重评共用此函数。
    """
    scene_set = set(scenes or [])
    saas_keys = set(saas_signals or {})
    social = social or {}
    job_urls = job_urls or []
    source_names = {r.get("source") for r in (sources or []) if r.get("source")}
    target_regions = {r.upper() for r in settings.TARGET_REGIONS}

    # D1 出海度：中国出海特征是最强证据，FB 私域/目标地区/官网次之
    overseas = (
        (45 if is_cn else 0)
        + (30 if fb_whatsapp else 0)
        + (15 if country and country.upper() in target_regions else 0)
        + (10 if website else 0)
    )

    # D2 WhatsApp 意向强度（补充需求 §4.1：CTWA/私域是黄金意向信号，权重最高）
    wa_number_count = len(whatsapp_numbers or [])
    whatsapp = (
        (25 if fb_whatsapp else 0)  # FB 主页 wa.me（CTWA 代理信号）——意向最强
        + (35 if whatsapp_hit else 0)
        + (15 if whatsapp_url else 0)
        + (15 if whatsapp_job else 0)
        + (10 if wa_number_count >= 2 else 0)  # 多分线 = 已规模化使用
        + (10 if "customer_service" in scene_set else 0)
        + (10 if "marketing" in scene_set else 0)
    )

    # D3 SaaS 需求度：工具信号命中即得分（可超 100 后封顶）
    saas = sum(SAAS_SIGNAL_POINTS.get(k, 0) for k in saas_keys if k in SAAS_SIGNAL_POINTS)
    if "saas" in scene_set:
        saas += 15

    # D4 企业规模（代理）：招聘岗位数 + 社媒广度 + 联系渠道完备度
    scale = 0
    if len(job_urls) >= 4:
        scale += 50
    elif len(job_urls) >= 2:
        scale += 40
    elif len(job_urls) == 1:
        scale += 25
    if len(social) >= 3:
        scale += 35
    elif len(social) == 2:
        scale += 25
    elif len(social) == 1:
        scale += 15
    scale += (10 if website else 0) + (10 if email else 0)
    if phone_e164 or phone_raw:
        scale += 5

    # D5 营销活跃度（代理）：在投广告 + 营销文案 + 多渠道曝光
    marketing = 0
    if "meta_ads" in source_names:
        marketing += 40
    if "marketing" in scene_set:
        marketing += 20
    if len(source_names) >= 3:
        marketing += 25
    elif len(source_names) == 2:
        marketing += 15
    if len(social) >= 3:
        marketing += 15
    elif len(social) == 2:
        marketing += 10

    # D6 联系人质量：数量 + 决策层深度（tier1 = CEO/Founder/GM）
    contact = 0
    if contacts_count >= 3:
        contact += 60
    elif contacts_count == 2:
        contact += 50
    elif contacts_count == 1:
        contact += 30
    contact += (40 if has_tier1 else 0) + (20 if has_tier2 else 0)
    if contacts_count == 0 and email:
        contact += 10

    dims = {
        "overseas": min(100, overseas),
        "whatsapp": min(100, whatsapp),
        "saas": min(100, saas),
        "scale": min(100, scale),
        "marketing": min(100, marketing),
        "contact": min(100, contact),
    }

    weights = effective_dim_weights()
    total = round(sum(dims[k] * weights.get(k, DIM_WEIGHTS[k]) for k in dims) / 100)
    total = max(0, min(100, total))
    return total, dims, grade_of(total)


def apply_score(
    lead: Any,
    *,
    contacts_count: int = 0,
    has_tier1: bool = False,
    has_tier2: bool = False,
) -> tuple[int, int, str]:
    """对 ORM Lead 行评分并写回 score/score_signals/grade。

    返回 (旧分, 新分, 新分级)，供事件 diff 使用。
    """
    old_score = lead.score
    total, dims, grade = score_lead_inputs(
        is_cn=lead.is_cn,
        fb_whatsapp=lead.fb_whatsapp,
        country=lead.country,
        website=lead.website,
        whatsapp_hit=lead.whatsapp_hit,
        whatsapp_url=lead.whatsapp_url,
        whatsapp_job=lead.whatsapp_job,
        scenes=lead.scenes,
        whatsapp_numbers=lead.whatsapp_numbers,
        saas_signals=lead.saas_signals,
        job_urls=lead.job_urls,
        social=lead.social,
        email=lead.email,
        phone_raw=lead.phone_raw,
        phone_e164=lead.phone_e164,
        sources=lead.sources,
        contacts_count=contacts_count,
        has_tier1=has_tier1,
        has_tier2=has_tier2,
    )
    lead.score = total
    lead.score_signals = dims
    lead.grade = grade
    return old_score, total, grade
