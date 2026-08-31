"""商机意向分 V3：加分制（PRD §五 MVP 口径，2026-08-31 重设计定稿）。

Lead Score = 命中信号分值直接相加，封顶 100
分级：80-100=S（立即跟进） 60-79=A（高潜力） 40-59=B（培育池） 0-39=C

设计说明（spec §3）：
- 分数含义是「这家公司现在有多需要你」（WhatsApp 商机意向），不是企业质量分
- 每 1 分可溯源到一条证据（score_breakdown items，销售直读"这分怎么来的"）
- 六维加权（75% 权重押在无数据通道、旗舰锚点仅 63 分、全库最高 36 分全员 C）
  已废弃——那是 PRD 明确留给「成交数据回传后」的成熟阶段形态
- SaaS 类目信号（crm/helpdesk/chatbot…）与规模信号（岗位数/社媒广度/联系人）
  不进主分：它们回答「应该卖什么」（三问之二），唯一例外 wa_bsp（竞品栈=迁移意向）
- score_signals JSON 存 {命中信号键: 分值}（v3 口径；旧六维格式废弃）
"""

from __future__ import annotations

from typing import Any

# ---------- 信号分值表（spec §3.2 定稿；改值必须过 spec 评审） ----------

INTENT_SIGNALS: list[tuple[str, str, int]] = [
    ("ctwa_ad", "CTWA 私域获客（FB 主页挂 WhatsApp + 在投广告）", 40),
    ("wa_ops_job", "在招 WhatsApp 运营岗", 30),
    ("wa_bsp_competitor", "已用其他 WhatsApp SaaS（替换商机）", 30),
    ("site_whatsapp", "官网 WhatsApp 入口", 25),
    ("overseas_cs_job", "在招海外/英文客服岗", 20),
    ("wa_business", "WhatsApp Business 业务号", 15),
    ("meta_ads_running", "在投 Meta 海外广告", 15),
    ("overseas_biz", "出海业务证据", 15),
    # 出海 SaaS 线（2026-09-01 深度复核补）：定位句是「WA 消息 + 出海 SaaS」两条
    # 产品线——已在为 SaaS 付费（买入强度≥40，与推荐阈值同口径）的出海公司是
    # SaaS 线最确定的买家，此前 0 分进 C 级、永远进不了今日商机（半条产品线失效）
    ("saas_buying", "SaaS 工具买入成规模（已在为 CRM/客服系统付费）", 15),
    ("overseas_site", "海外独立站", 10),
    ("crm_job", "在招 CRM/客服系统运营岗", 10),
    ("three_markets", "覆盖 ≥3 国市场", 10),
    ("multi_numbers", "多 WhatsApp 分线（≥2 号码）", 10),
    ("social_active", "海外社媒活跃（≥2 平台）", 5),
]

INTENT_LABELS_ZH: dict[str, str] = {k: label for k, label, _ in INTENT_SIGNALS}


def grade_of(score: int) -> str:
    """总分 → S/A/B/C 分级（阈值 PRD 口径不变）。"""
    if score >= 80:
        return "S"
    if score >= 60:
        return "A"
    if score >= 40:
        return "B"
    return "C"


def score_lead_inputs(
    *,
    fb_whatsapp: bool = False,
    website: str | None = None,
    whatsapp_hit: bool = False,
    whatsapp_job: bool = False,
    whatsapp_numbers: list[str] | None = None,
    wa_business: bool = False,
    saas_signals: dict[str, Any] | None = None,
    social: dict[str, Any] | None = None,
    sources: list[dict[str, Any]] | None = None,
    target_countries: list[str] | None = None,
    overseas_signals: dict[str, list[str]] | None = None,
    job_signals: dict[str, dict[str, Any]] | None = None,
    ad_count: int = 0,
    # 以下参数保留签名兼容（apply_score 调用方在传），v3 不参与打分
    is_cn: bool = False,  # noqa: ARG001
    country: str | None = None,  # noqa: ARG001
    whatsapp_url: str | None = None,  # noqa: ARG001
    scenes: list[str] | None = None,  # noqa: ARG001
    job_urls: list[str] | None = None,  # noqa: ARG001
    email: str | None = None,  # noqa: ARG001
    phone_raw: str | None = None,  # noqa: ARG001
    phone_e164: str | None = None,  # noqa: ARG001
    contacts_count: int = 0,  # noqa: ARG001
    has_tier1: bool = False,  # noqa: ARG001
    has_tier2: bool = False,  # noqa: ARG001
) -> tuple[int, list[dict], str]:
    """意向分 v3 纯函数：返回 (总分, 命中信号 items, 分级)。无 IO。

    互斥规则：CTWA（+40）= fb_whatsapp ∧ 在投证据（meta_ads 来源或 ad_count>0），
    成立时替代「在投广告」（+15）——CTWA 已隐含在投事实。
    overseas_biz = overseas_signals 非空；overseas_site = website ∧ 出海证据（可叠加）。
    """
    job_keys = set(job_signals or {})
    saas_keys = set(saas_signals or {})
    source_names = {r.get("source") for r in (sources or []) if r.get("source")}
    ad_running = "meta_ads" in source_names or ad_count > 0
    ctwa = fb_whatsapp and ad_running
    ov = overseas_signals or {}
    markets = {c.upper() for c in (target_countries or []) if c}
    markets |= {m.upper() for m in ov.get("markets", []) if m}
    # SaaS 买入强度（与推荐阈值同口径，表在 scenes 单一来源）——wa_bsp 已有专列
    # 信号（+30），强度里再叠加会双计，扣除后判定「其余类目」是否成规模
    from app.collectors.scenes import SAAS_CATEGORY_POINTS

    saas_strength_ex_bsp = sum(
        SAAS_CATEGORY_POINTS.get(k, 0) for k in saas_keys if k != "wa_bsp"
    )

    matched: dict[str, bool] = {
        "ctwa_ad": ctwa,
        "wa_ops_job": "wa_ops" in job_keys or whatsapp_job,
        "wa_bsp_competitor": "wa_bsp" in saas_keys,
        "site_whatsapp": whatsapp_hit,
        "overseas_cs_job": "overseas_cs" in job_keys,
        "wa_business": wa_business,
        "meta_ads_running": ad_running and not ctwa,
        "overseas_biz": bool(ov),
        "saas_buying": saas_strength_ex_bsp >= 40,
        "overseas_site": bool(website) and bool(ov),
        "crm_job": "crm_ops" in job_keys,
        "three_markets": len(markets) >= 3,
        "multi_numbers": len(whatsapp_numbers or []) >= 2,
        "social_active": len(social or {}) >= 2,
    }
    items = [
        {"key": key, "label": label, "points": points}
        for key, label, points in INTENT_SIGNALS
        if matched[key]
    ]
    total = min(100, sum(it["points"] for it in items))
    return total, items, grade_of(total)


def bonus_breakdown(**kwargs: Any) -> dict[str, Any]:
    """兼容包装（历史迁移 f8a4c31e9d02 import 此名）：返回 v3 明细。

    语义与旧版一致——{"total": 总分, "items": [{key,label,points}]}，
    区别只是它现在就是主分口径（不再是六维之外的"参考层"）。
    """
    total, items, _grade = score_lead_inputs(**kwargs)
    return {"total": total, "items": items}


def apply_score(
    lead: Any,
    *,
    contacts_count: int = 0,  # noqa: ARG001 — 联系人不进意向分，进三问（spec §3.2）
    has_tier1: bool = False,  # noqa: ARG001
    has_tier2: bool = False,  # noqa: ARG001
) -> tuple[int, int, str]:
    """对 ORM Lead 行评分并写回 score/score_signals/score_breakdown/grade。

    返回 (旧分, 新分, 新分级)，供事件 diff 使用。
    评分 / ICP 门 / export_type 同点派生的架构不变。
    """
    old_score = lead.score
    total, items, grade = score_lead_inputs(
        fb_whatsapp=lead.fb_whatsapp,
        website=lead.website,
        whatsapp_hit=lead.whatsapp_hit,
        whatsapp_job=lead.whatsapp_job,
        whatsapp_numbers=lead.whatsapp_numbers,
        wa_business=getattr(lead, "wa_business", False),
        saas_signals=lead.saas_signals,
        social=lead.social,
        sources=lead.sources,
        target_countries=getattr(lead, "target_countries", None),
        overseas_signals=getattr(lead, "overseas_signals", None),
        job_signals=getattr(lead, "job_signals", None),
        ad_count=getattr(lead, "ad_count", 0) or 0,
    )
    lead.score = total
    # v3 口径：命中信号键 → 分值（旧六维格式废弃；历史值随重评被覆盖）
    lead.score_signals = {it["key"]: it["points"] for it in items}
    lead.score_breakdown = {"total": total, "items": items}
    lead.grade = grade
    # ICP 门：资格与评分同点重算（upsert/富化/联系人变更都走到这里）
    from app.collectors.icp import compute_icp_status_of

    lead.icp_status = compute_icp_status_of(lead)
    # 出海业务类型：同为行属性派生，同点重算
    from app.collectors.overseas import derive_export_type

    lead.export_type = derive_export_type(
        industry=getattr(lead, "industry", None),
        overseas_signals=getattr(lead, "overseas_signals", None),
        target_countries=getattr(lead, "target_countries", None),
        job_signals=getattr(lead, "job_signals", None),
        sources=lead.sources,
    )
    return old_score, total, grade
