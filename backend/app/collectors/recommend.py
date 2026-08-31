"""产品推荐 & 销售建议：基于企业画像的规则引擎（V1 不接 LLM）。

输入全部来自 Lead 行属性（whatsapp_hit/url/job、scenes、saas_signals、industry），
纯函数、无 IO，列表页可逐行调用。SaaS 买入强度由 saas_signals 内部计算
（SAAS_CATEGORY_POINTS，2026-08-31 六维下线后从 scoring 迁来）。
"""

from __future__ import annotations

from typing import Any

from app.collectors.scenes import SAAS_CATEGORY_POINTS, SAAS_LABELS_ZH

__all__ = [
    "SAAS_CATEGORY_POINTS",  # 从 scenes 重导出：历史 import 方（tests 等）不断
    "NEED_TYPES",
    "PRODUCTS",
    "detect_need_types",
    "recommend_products",
    "sales_suggestion",
]

# ---------- 产品目录（双业务线：WA 消息/SaaS + 广告代理） ----------

PRODUCTS: dict[str, str] = {
    # WhatsApp 消息线（BSP 主线）
    "wa_cs": "WhatsApp 客服 SaaS",
    "marketing_message": "WhatsApp 营销消息",
    "transactional_message": "WhatsApp 交易通知",
    "ai_cs": "WhatsApp AI 智能客服",
    "wa_api": "WhatsApp Business API",
    # 出海 SaaS 线
    "overseas_saas": "出海 SaaS 方案（CRM/客服/营销自动化）",
    # 广告代理线（Meta/TikTok 一级代理）
    "ads_agency": "Meta/TikTok 广告代理投放",
}

# ---------- 需求类型（§4.4 A-E + F 广告线，对应可售产品与卖点） ----------

NEED_TYPES: dict[str, dict[str, str]] = {
    "messaging": {"label": "消息需求", "selling": "价格/送达率/稳定性"},
    "api_upgrade": {"label": "API 升级需求", "selling": "多坐席/企业管理（个人号/Business App 迁移）"},
    "customer_service": {"label": "客服需求", "selling": "多坐席客服 SaaS"},
    "marketing": {"label": "营销需求", "selling": "Marketing Message/自动化触达"},
    "private_domain": {"label": "私域需求", "selling": "私域运营方案（社群+营销活动）"},
    # F 广告线（2026-08-31 双业务线）：与 WA 使用无关，在投广告即成立
    "ads": {"label": "广告投放需求", "selling": "Meta/TikTok 一级代理开户/优化/返点"},
}


def detect_need_types(
    *,
    whatsapp_hit: bool,
    whatsapp_url: str | None,
    whatsapp_numbers: list[str] | None = None,
    whatsapp_job: bool = False,
    scenes: list[str] | None = None,
    saas_signals: dict[str, Any] | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """需求类型识别（§4.4）：返回 [{type, label, selling}]，按证据强度排序。

    A 消息需求：官网 WhatsApp + 交易/营销场景（有量就有消息成本诉求）
    B API 升级：个人号/Business App 使用中（无 SaaS 工具痕迹 = 大概率个人号）；
      已在用竞品 BSP（wa_bsp）= 替换商机，排最前（卖点改成替换口径）
    C 客服需求：WhatsApp + 客服场景/在招客服/多分线
    D 营销需求：meta_ads 在投 + WhatsApp + 独立站
    E 私域需求：多 WhatsApp 号 + 营销场景（社群/活动运营形态）
    F 广告投放：在投 Meta 广告（与 WA 使用无关——双业务线同客双报价）
    """
    scene_set = set(scenes or [])
    saas = set(saas_signals or {})
    # F3（2026-09-01 深度复核）：WA 使用判定必须含 whatsapp_numbers——
    # meta_ads 主通道形态 = 官网 whatsapp_hit=False + 主页探测多条号码 +
    # fb_whatsapp，旧口径把 CTWA 大卖判成「不用 WA」，需求类型 B/C/E 全漏
    uses_wa = bool(whatsapp_hit or whatsapp_url or whatsapp_numbers)
    multi_line = len(whatsapp_numbers or []) >= 2
    ad_running = any(r.get("source") == "meta_ads" for r in (sources or []))
    using_bsp = "wa_bsp" in saas  # 已在用 WhatsApp SaaS 竞品（§4.1 替换商机）
    out: list[dict[str, str]] = []

    if ad_running:
        out.append({"type": "ads", **NEED_TYPES["ads"]})
    if using_bsp:
        out.append({
            "type": "api_upgrade",
            **NEED_TYPES["api_upgrade"],
            "selling": "竞品替换：迁移方案/更优价格与送达率（检测到在用其他 WhatsApp SaaS）",
        })
    if uses_wa and ("transactional" in scene_set or "marketing" in scene_set):
        out.append({"type": "messaging", **NEED_TYPES["messaging"]})
    if uses_wa and not saas:
        out.append({"type": "api_upgrade", **NEED_TYPES["api_upgrade"]})
    if uses_wa and ("customer_service" in scene_set or whatsapp_job or multi_line):
        out.append({"type": "customer_service", **NEED_TYPES["customer_service"]})
    if uses_wa and ad_running:
        out.append({"type": "marketing", **NEED_TYPES["marketing"]})
    if multi_line and "marketing" in scene_set:
        out.append({"type": "private_domain", **NEED_TYPES["private_domain"]})
    # 同类型去重（using_bsp 与"无 SaaS 痕迹"可能都产出 api_upgrade）
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for item in out:
        if item["type"] not in seen:
            seen.add(item["type"])
            deduped.append(item)
    return deduped


# 行业里偏电商的关键词（营销消息推荐的条件之一）
_ECOMMERCE_TOKENS = ("电商", "零售", "电子", "美妆", "服装", "家居", "e-commerce", "retail", "shopping")


def _uses_whatsapp(whatsapp_hit: bool, whatsapp_url: str | None, whatsapp_numbers: list[str] | None = None) -> bool:
    """WA 使用判定（F3）：hit/url/numbers 三口径任一——numbers 覆盖 meta_ads 主页探测形态。"""
    return bool(whatsapp_hit or whatsapp_url or whatsapp_numbers)


def recommend_products(
    *,
    whatsapp_hit: bool,
    whatsapp_url: str | None,
    whatsapp_job: bool,
    scenes: list[str] | None,
    saas_signals: dict[str, Any] | None,
    industry: str | None = None,
    sources: list[dict[str, Any]] | None = None,
    whatsapp_numbers: list[str] | None = None,
) -> list[dict[str, Any]]:
    """返回 [{key, name, reason, priority(1=最强)}]，按 priority 升序，可为空。

    双业务线（2026-08-31）：WA 消息线 + 出海 SaaS 线 + 广告代理线，
    同一客户可同时命中多线（§十四.6 线索双需求归属）。
    """
    scene_set = set(scenes or [])
    saas = set(saas_signals or {})
    saas_strength = sum(SAAS_CATEGORY_POINTS.get(k, 0) for k in saas)
    wa = _uses_whatsapp(whatsapp_hit, whatsapp_url, whatsapp_numbers)
    ad_running = any(r.get("source") == "meta_ads" for r in (sources or []))
    recs: list[dict[str, Any]] = []

    # 规则一：在用 WA +（客服场景 或 在招 WA 客服岗）→ 客服 SaaS
    if wa and ("customer_service" in scene_set or whatsapp_job):
        recs.append(
            {
                "key": "wa_cs",
                "name": PRODUCTS["wa_cs"],
                "reason": "已检测到 WhatsApp 使用痕迹，且存在客服场景/在招客服岗位，客服协作需求明确",
                "priority": 1,
            }
        )

    # 规则二：在用 WA + 营销场景 +（交易场景 或 电商行业）→ 营销消息
    if wa and "marketing" in scene_set and (
        "transactional" in scene_set
        or any(tok in (industry or "").lower() for tok in _ECOMMERCE_TOKENS)
    ):
        recs.append(
            {
                "key": "marketing_message",
                "name": PRODUCTS["marketing_message"],
                "reason": "官网有促销/活动类关键词，且业务带交易属性，适合批量营销触达",
                "priority": 2,
            }
        )

    # 规则三：在用 WA + 交易场景 → 交易通知消息
    if wa and "transactional" in scene_set:
        recs.append(
            {
                "key": "transactional_message",
                "name": PRODUCTS["transactional_message"],
                "reason": "存在订单/物流/支付类关键词，订单通知是刚需场景",
                "priority": 2,
            }
        )

    # 规则四：SaaS 买入强度较高 +（AI 信号 或 客服场景）→ AI 智能客服
    if saas_strength >= 40 and ("ai_service" in saas or "customer_service" in scene_set):
        recs.append(
            {
                "key": "ai_cs",
                "name": PRODUCTS["ai_cs"],
                "reason": "检测到 AI/智能客服相关信号，客服自动化升级意向明显",
                "priority": 3,
            }
        )

    # 规则五（出海 SaaS 线）：SaaS 需求信号成规模（≥2 类或买入强度≥40）→ SaaS 方案
    if len(saas) >= 2 or saas_strength >= 40:
        recs.append(
            {
                "key": "overseas_saas",
                "name": PRODUCTS["overseas_saas"],
                "reason": "官网呈现 CRM/客服/营销自动化等多类工具需求信号，可推自研出海 SaaS 套件",
                "priority": 3,
            }
        )

    # 规则六（广告代理线）：在投 Meta 广告 → 一级代理投放服务
    # （双业务线同客双报价：消息/SaaS 之外的第二条腿）
    if ad_running:
        recs.append(
            {
                "key": "ads_agency",
                "name": PRODUCTS["ads_agency"],
                "reason": "检测到在投 Meta 广告，可切入代理开户/优化/返点（与消息线同客双报价）",
                "priority": 2,
            }
        )

    return sorted(recs, key=lambda r: r["priority"])


def sales_suggestion(
    *,
    grade: str,
    whatsapp_url: str | None,
    whatsapp_job: bool,
    saas_signals: dict[str, Any] | None,
    has_tier1_contact: bool = False,
    products: list[dict[str, Any]] | None = None,
) -> str:
    """规则模板拼接的销售建议文案（详情页展示，非 LLM）。"""
    parts: list[str] = []

    if grade == "S":
        parts.append("S 级高价值线索，建议销售当天跟进")
    elif grade == "A":
        parts.append("A 级高潜力线索，建议 3 个工作日内跟进")
    elif grade == "B":
        parts.append("B 级培育线索，可进入销售培育池择机跟进")
    else:
        parts.append("C 级线索，暂不优先跟进")

    if whatsapp_url:
        parts.append("已发现 WhatsApp 入口，可直接以 WhatsApp 建联")
    elif whatsapp_job:
        parts.append("在招 WhatsApp 相关岗位，说明正在搭建私域客服团队")

    saas_labels = [SAAS_LABELS_ZH[k] for k in (saas_signals or {}) if k in SAAS_LABELS_ZH]
    if saas_labels:
        parts.append(f"检测到 SaaS 需求信号：{'、'.join(saas_labels[:3])}")

    if has_tier1_contact:
        parts.append("已有决策层联系人，建议直接触达关键人")

    if products:
        top = products[0]
        parts.append(f"建议主推：{top['name']}，切入点：{top['reason'].split('，')[0]}")

    return "；".join(parts)
