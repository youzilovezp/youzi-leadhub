"""三问生成器（PRD 核心价值）：为什么需要你 / 应该卖什么 / 应该找谁。

纯函数组装层（无 IO、无新增采集）：输入 Lead 行属性 + 联系人序列，
输出可直接序列化的三问结构。「为什么」来自意向分明细（score_breakdown），
「卖什么」来自需求类型/推荐产品/场景，「找谁」两档——先真实联系人/WA 号码，
没有则按信号规则派生目标角色（永不空：销售至少知道该找什么职位的人）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.collectors.recommend import detect_need_types, recommend_products
from app.collectors.scenes import SAAS_LABELS_ZH, SCENE_LABELS_ZH


def _evidence_url(lead: Any, key: str) -> str | None:
    job_urls = list(getattr(lead, "job_urls", None) or [])
    if key == "site_whatsapp":
        return getattr(lead, "whatsapp_url", None)
    if key in ("wa_ops_job", "overseas_cs_job", "crm_job"):
        return job_urls[0] if job_urls else None
    if key in ("overseas_biz", "overseas_site"):
        return getattr(lead, "website", None)
    if key == "ctwa_ad":
        return getattr(lead, "website", None) or getattr(lead, "whatsapp_url", None)
    return None


# who 角色派生：信号 → 该找谁（PRD §七 联系人三级优先级的运营化落地）
_ROLE_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("wa_ops",), "WhatsApp/私域运营负责人", "在招 WhatsApp 运营岗 → 招聘页/官网联系页"),
    (("overseas_cs",), "海外客服负责人", "在招海外/英文客服岗 → 招聘页/官网联系页"),
    (("crm", "helpdesk", "chatbot", "ai_service", "marketing_automation", "omnichannel", "brand_stack"),
     "CRM/客服系统负责人", "检测到 SaaS 工具栈 → 官网联系页"),
    (("fb_whatsapp",), "海外营销负责人", "FB 主页挂 WhatsApp 私域 → FB 主页/官网联系页"),
]
_FALLBACK_ROLE = ("海外业务负责人", "官网联系页 / 招聘页")


def _derive_roles(lead: Any) -> list[dict[str, str]]:
    job_keys = set(getattr(lead, "job_signals", None) or {})
    saas_keys = set(getattr(lead, "saas_signals", None) or {})
    roles: list[dict[str, str]] = []
    for keys, role, hint in _ROLE_RULES:
        if any(k in job_keys or k in saas_keys for k in keys):
            if all(r["role"] != role for r in roles):
                roles.append({"role": role, "hint": hint})
    roles.append({"role": _FALLBACK_ROLE[0], "hint": _FALLBACK_ROLE[1]})
    return roles


def _top_contacts(contacts: Sequence[Any] | None) -> list[dict[str, Any]]:
    order = {"tier1": 0, "tier2": 1, "tier3": 2}
    items = [
        {
            "name": getattr(c, "name", None) or "（待补全）",
            "title": getattr(c, "job_title", None),
            "seniority": getattr(c, "seniority", None),
            "email": getattr(c, "email", None),
        }
        for c in (contacts or [])
    ]
    items.sort(key=lambda x: order.get(x["seniority"] or "", 9))
    return items[:3]


def build_three_questions(lead: Any, *, contacts: Sequence[Any] | None = None) -> dict[str, Any]:
    """行属性 + 联系人 → 三问结构（详见模块 docstring / spec §六）。"""
    breakdown = getattr(lead, "score_breakdown", None) or {}
    items = sorted(
        breakdown.get("items", []),
        key=lambda it: -int(it.get("points", 0)),
    )
    why = [
        {
            "key": it["key"],
            "label": it.get("label", it["key"]),
            "points": int(it.get("points", 0)),
            "evidence_url": _evidence_url(lead, it["key"]),
        }
        for it in items[:3]
    ]

    products = recommend_products(
        whatsapp_hit=bool(getattr(lead, "whatsapp_hit", False)),
        whatsapp_url=getattr(lead, "whatsapp_url", None),
        whatsapp_job=bool(getattr(lead, "whatsapp_job", False)),
        scenes=list(getattr(lead, "scenes", None) or []),
        saas_signals=dict(getattr(lead, "saas_signals", None) or {}),
        industry=getattr(lead, "industry", None),
        sources=list(getattr(lead, "sources", None) or []),
    )
    need_types = detect_need_types(
        whatsapp_hit=bool(getattr(lead, "whatsapp_hit", False)),
        whatsapp_url=getattr(lead, "whatsapp_url", None),
        whatsapp_numbers=list(getattr(lead, "whatsapp_numbers", None) or []),
        whatsapp_job=bool(getattr(lead, "whatsapp_job", False)),
        scenes=list(getattr(lead, "scenes", None) or []),
        saas_signals=dict(getattr(lead, "saas_signals", None) or {}),
        sources=list(getattr(lead, "sources", None) or []),
    )
    what = {
        "need_types": need_types,
        "products": products,
        "scenes": [SCENE_LABELS_ZH.get(s, s) for s in (getattr(lead, "scenes", None) or [])],
        "saas_signals": [
            f"{SAAS_LABELS_ZH.get(k, k)}（在用）" if k == "wa_bsp" else SAAS_LABELS_ZH.get(k, k)
            for k in (getattr(lead, "saas_signals", None) or {})
        ],
    }

    wa_numbers = list(getattr(lead, "whatsapp_numbers", None) or [])[:3]
    who = {
        "contacts": _top_contacts(contacts),
        "whatsapp_numbers": wa_numbers,
        "whatsapp_url": getattr(lead, "whatsapp_url", None),
        "roles": _derive_roles(lead),
    }

    # 齐备度（spec §六）：why≥2 证据 ∧ what≥1 产品 ∧ who 有任一答案
    who_ok = bool(who["contacts"] or wa_numbers or who["whatsapp_url"])
    complete = len(why) >= 2 and len(products) >= 1 and who_ok
    return {"why": why, "what": what, "who": who, "complete": complete}
