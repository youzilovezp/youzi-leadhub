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
    "wa_bsp": 30,  # WhatsApp SaaS 竞品在用（§4.1 替换商机，最强 SaaS 信号）
}

# ---------- MVP 加分制（PRD §五：简单、可解释、可调参） ----------
# 13 条信号加分表——与六维加权并存：六维是主分（成熟阶段口径），
# 加分明细是可解释层（销售直读"这分怎么来的"，调参只改这张表）。
BONUS_SIGNALS: list[tuple[str, str, int]] = [
    ("ctwa_ad", "CTWA 广告（FB 主页挂 wa.me 代理信号）", 40),
    ("site_whatsapp", "官网存在 WhatsApp 入口", 30),
    ("wa_ops_job", "招聘 WhatsApp 运营/客服", 30),
    ("wa_bsp_competitor", "已使用其他 WhatsApp SaaS（竞品替换商机）", 30),
    ("overseas_cs_job", "招聘海外/英文客服", 20),
    ("wa_business", "使用 WhatsApp Business", 15),
    ("meta_ads", "在投 Meta 广告", 15),
    ("overseas_biz", "海外业务特征", 15),
    ("multi_numbers", "多个 WhatsApp 分线（规模化使用）", 10),
    ("overseas_site", "海外独立站（多语言/电商建站栈）", 10),
    ("crm_job", "招聘 CRM/Customer Success", 10),
    ("three_markets", "投放/提及 ≥3 国市场", 10),
    ("social_active", "海外社媒活跃（≥3 平台）", 5),
]

BONUS_LABELS_ZH: dict[str, str] = {k: label for k, label, _ in BONUS_SIGNALS}


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


def bonus_breakdown(
    *,
    fb_whatsapp: bool = False,
    whatsapp_hit: bool = False,
    whatsapp_numbers: list[str] | None = None,
    job_signals: dict[str, Any] | None = None,
    saas_signals: dict[str, Any] | None = None,
    wa_business: bool = False,
    sources: list[dict[str, Any]] | None = None,
    ad_count: int = 0,
    is_cn: bool = False,
    overseas_signals: dict[str, list[str]] | None = None,
    target_countries: list[str] | None = None,
    social: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """MVP 加分制明细（PRD §五 13 条）：返回 {"total": 加分制总分, "items": [...]}。

    items 只含命中项 [{key, label, points}]——销售直读"这分怎么来的"。
    与六维总分并存：六维是主分/主分级，加分制是可解释参考口径。
    """
    job_keys = set(job_signals or {})
    saas_keys = set(saas_signals or {})
    source_names = {r.get("source") for r in (sources or []) if r.get("source")}
    overseas_signals = overseas_signals or {}
    # 投放/提及国家合并计数（meta_ads target_countries + 官网 markets 提及）
    bonus_market_set = {c.upper() for c in (target_countries or []) if c}
    bonus_market_set |= {m.upper() for m in overseas_signals.get("markets", []) if m}
    social = social or {}

    matched: dict[str, bool] = {
        "ctwa_ad": fb_whatsapp,
        "site_whatsapp": whatsapp_hit,
        "wa_ops_job": "wa_ops" in job_keys,
        "wa_bsp_competitor": "wa_bsp" in saas_keys,
        "overseas_cs_job": "overseas_cs" in job_keys,
        "wa_business": wa_business,
        "meta_ads": ("meta_ads" in source_names) or ad_count > 0,
        "overseas_biz": is_cn or len(overseas_signals) >= 2,
        "multi_numbers": len(whatsapp_numbers or []) >= 2,
        "overseas_site": bool(overseas_signals.get("languages")) or bool(overseas_signals.get("ecommerce")),
        "crm_job": "crm_ops" in job_keys,
        "three_markets": len(bonus_market_set) >= 3,
        "social_active": len(social) >= 3,
    }
    items = [
        {"key": key, "label": label, "points": points}
        for key, label, points in BONUS_SIGNALS
        if matched.get(key)
    ]
    total = min(100, sum(it["points"] for it in items))
    return {"total": total, "items": items}


def score_lead_inputs(
    *,
    is_cn: bool = False,  # noqa: ARG001 — 保留参数兼容旧迁移回填；资格判定已移交 ICP 门
    fb_whatsapp: bool = False,
    country: str | None = None,
    website: str | None = None,
    whatsapp_hit: bool = False,
    whatsapp_url: str | None = None,
    whatsapp_job: bool = False,
    scenes: list[str] | None = None,
    whatsapp_numbers: list[str] | None = None,
    wa_business: bool = False,
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
    target_countries: list[str] | None = None,
    overseas_signals: dict[str, list[str]] | None = None,
    job_signals: dict[str, dict[str, Any]] | None = None,
    ad_count: int = 0,
) -> tuple[int, dict[str, int], str]:
    """六维评分纯函数：返回 (总分, {维度: 维度分}, 分级)。

    所有输入都是行属性，无 IO；迁移回填与 ORM 重评共用此函数。
    新增输入（PRD §4.2/§4.3 信号体系补课，向后兼容默认空）：
    - target_countries：投放国家列表（≥3 国加分，§4.2）
    - overseas_signals：出海信号 {键: [证据]}（命中类数参与出海度）
    - job_signals：招聘信号细分 {键: {label, points}}（§4.3 分值）
    - ad_count：累计在投广告数（营销活跃度）
    """
    scene_set = set(scenes or [])
    saas_keys = set(saas_signals or {})
    social = social or {}
    job_urls = job_urls or []
    source_names = {r.get("source") for r in (sources or []) if r.get("source")}
    target_regions = {r.upper() for r in settings.TARGET_REGIONS}
    ov_kinds = len(overseas_signals or {})
    job_sig_points = sum(
        int(v.get("points", 0))
        for v in (job_signals or {}).values()
        if isinstance(v, dict)
    )

    # D1 出海度（2026-08-31 口径修正）：中国资格由 ICP 二重门承担（collectors/icp.py），
    # 维度内只量出海深度——FB 私域最强证据；投放/提及 ≥3 国 +10（官网 markets
    # 提及与 meta_ads 投放国家合并计数）；出海信号每命中一类 +7（最多 +35）
    market_set = {c.upper() for c in (target_countries or []) if c}
    market_set |= {m.upper() for m in (overseas_signals or {}).get("markets", []) if m}
    overseas = (
        (30 if fb_whatsapp else 0)
        + (15 if country and country.upper() in target_regions else 0)
        + (10 if website else 0)
        + (10 if len(market_set) >= 3 else 0)
        + min(35, ov_kinds * 7)
    )

    # D2 WhatsApp 意向强度（补充需求 §4.1：CTWA/私域是黄金意向信号，权重最高）
    # §4.3 招聘细分：只有 wa_ops（WhatsApp 运营/客服岗位）进 WhatsApp 维——
    # whatsapp_job 与 wa_ops 是同一事实（job_posting 按标题分类置位），只计一次；
    # 其余招聘信号（海外客服/CRM/海外销售）走规模维，不混入 WhatsApp 维
    wa_number_count = len(whatsapp_numbers or [])
    whatsapp = (
        (25 if fb_whatsapp else 0)  # FB 主页 wa.me（CTWA 代理信号）——意向最强
        + (35 if whatsapp_hit else 0)
        + (15 if whatsapp_url else 0)
        + (15 if whatsapp_job else 0)  # 在招 WhatsApp 运营/客服岗（= wa_ops）
        + (15 if wa_business else 0)  # WhatsApp Business 业务号（§4.1 +15~20）
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
    # §4.3 招聘信号强度：overseas_cs/social_ops/crm_ops/overseas_sales 的
    # 分值折半计入规模维（团队扩张代理）——wa_ops 已计入 WhatsApp 维不重复算
    non_wa_points = job_sig_points - int(
        (job_signals or {}).get("wa_ops", {}).get("points", 0)
    )
    scale += min(20, non_wa_points // 2)

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
    # 广告量分级（§4.1）：累计在投广告 ≥5 条 = 持续投放获客
    if ad_count >= 5:
        marketing += 15
    elif ad_count >= 2:
        marketing += 8

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
        wa_business=getattr(lead, "wa_business", False),
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
        target_countries=getattr(lead, "target_countries", None),
        overseas_signals=getattr(lead, "overseas_signals", None),
        job_signals=getattr(lead, "job_signals", None),
        ad_count=getattr(lead, "ad_count", 0) or 0,
    )
    lead.score = total
    lead.score_signals = dims
    lead.grade = grade
    # ICP 二重门（2026-08-31 业务重构）：资格与评分同点重算——upsert/富化/联系人
    # 变更都会走到这里，icp_status 始终与行属性一致
    from app.collectors.icp import compute_icp_status_of

    lead.icp_status = compute_icp_status_of(lead)
    # 出海业务类型（§8 出海画像）：同为行属性派生，同点重算（2026-08-31 巡检接线
    # ——此前 export_type 列无任何写入方，恒空）
    from app.collectors.overseas import derive_export_type

    lead.export_type = derive_export_type(
        industry=getattr(lead, "industry", None),
        overseas_signals=getattr(lead, "overseas_signals", None),
        target_countries=getattr(lead, "target_countries", None),
        job_signals=getattr(lead, "job_signals", None),
        sources=lead.sources,
    )
    # MVP 加分制明细（§五）：与六维并存的可解释层
    lead.score_breakdown = bonus_breakdown(
        fb_whatsapp=lead.fb_whatsapp,
        whatsapp_hit=lead.whatsapp_hit,
        whatsapp_numbers=lead.whatsapp_numbers,
        job_signals=getattr(lead, "job_signals", None),
        saas_signals=lead.saas_signals,
        wa_business=getattr(lead, "wa_business", False),
        sources=lead.sources,
        ad_count=getattr(lead, "ad_count", 0) or 0,
        is_cn=lead.is_cn,
        overseas_signals=getattr(lead, "overseas_signals", None),
        target_countries=getattr(lead, "target_countries", None),
        social=lead.social,
    )
    return old_score, total, grade
