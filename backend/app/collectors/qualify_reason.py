"""AI 判定理由（方向 B）：把 score_breakdown + signals + three_questions 聚合成
「为什么是这个等级」的自然语言解释。

设计要点：
    - 模板拼接永远可用（无 IO，纯函数）—— LLM 失败时降级
    - LLM 增强可选，与 ai_analysis 同降级模式
    - cache_key = hash(score_signals + signals + icp_status + contacts_count)
      输入未变就复用，避免无谓的 LLM 调用
    - 输出 JSON 结构稳定：{summary, drivers[], blockers[], next_action,
                            generated_by, generated_at, cache_key}
    - 触发点：富化完成 / 评分变更 / 联系人变更 三处 hook（callers 负责调用）
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.collectors.icp import ICP_STATUS_LABELS_ZH
from app.collectors.intent import recommend_products
from app.collectors.recommend import detect_need_types, sales_suggestion
from app.collectors.scenes import SAAS_LABELS_ZH, SCENE_LABELS_ZH
from app.core.exceptions import BusinessError
from app.services import llm


# ---------- 模板拼接（纯函数，无 IO）----------


def _signal_type_label(t: str) -> str:
    """signal_type → 中文标签（与 crud/lead_signals.SIGNAL_TYPE_LABELS_ZH 同源口径）。"""
    from app.crud.lead_signals import SIGNAL_TYPE_LABELS_ZH

    return SIGNAL_TYPE_LABELS_ZH.get(t, t)


def _build_drivers(
    score_items: list[dict[str, Any]],
    signals: list[Any],
    signal_urls: dict[str, str] | None,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """drivers：命中证据（按分值降序），每条带 evidence_url（来自 signals 表）。

    signal_urls：调用方传入 {signal_type: evidence_url} 映射（来自 lead_signals 表
    的 evidence_url 聚合，按 signal_type 取第一条）。score_items 里的 key 与
    signals 里的 type 没有 1:1 映射（一个是「评分键」一个是「事实键」），所以
    采用「分数降序 + 取最近一条 signal」的方式拼装。
    """
    signal_urls = signal_urls or {}
    drivers: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for it in score_items[:top_n]:
        key = it.get("key", "")
        if not key or key in seen_keys:
            continue
        # 找第一条匹配 signal_type 的 evidence_url（score key → signal type 启发式）
        signal_type = _score_key_to_signal_type(key)
        evidence_url = signal_urls.get(signal_type) if signal_type else None
        drivers.append(
            {
                "key": key,
                "label": it.get("label", key),
                "points": int(it.get("points", 0)),
                "evidence_url": evidence_url,
            }
        )
        seen_keys.add(key)
    return drivers


_SCORE_KEY_TO_SIGNAL_TYPE: dict[str, str] = {
    # score key → 最近的 signal_type（线索证据链里的「事实键」）
    # 没有则不显示 evidence_url（前端显示为「-」）
    "site_whatsapp": "whatsapp_link",
    "ctwa_ad": "fb_whatsapp",
    "wa_ops_job": "job_signal",
    "overseas_cs_job": "job_signal",
    "crm_job": "job_signal",
    "meta_ads_running": "meta_ad",
    "wa_business": "wa_business",
    "multi_numbers": "whatsapp_number",
    "overseas_site": "domain_tld",
}


def _score_key_to_signal_type(score_key: str) -> str | None:
    return _SCORE_KEY_TO_SIGNAL_TYPE.get(score_key)


def _build_blockers(
    lead: Any,
    contacts: list[Any] | None,
) -> list[dict[str, Any]]:
    """blockers：扣分项 / 缺失证据 —— 解释「为什么是 C 级」。

    不重复 lead 已有字段（follow_status / icp_status 在前端的卡片展示），
    只列「应该建联但还没有」的事实缺口（无邮箱、无电话、无 WA 号码、无联系人）。
    """
    blockers: list[dict[str, Any]] = []
    missing: list[str] = []
    if not lead.email:
        missing.append("email")
    if not (lead.phone_e164 or lead.phone_raw):
        missing.append("phone")
    if not (lead.whatsapp_hit or lead.whatsapp_url or lead.whatsapp_numbers):
        missing.append("whatsapp")
    if missing:
        blockers.append(
            {
                "reason": "缺少关键联系方式",
                "missing": missing,
            }
        )
    if not (contacts or []):
        blockers.append(
            {
                "reason": "未识别出决策层联系人",
                "missing": ["tier1_contact"],
            }
        )
    return blockers


def _build_next_action(
    lead: Any,
    products: list[dict[str, Any]],
    contacts: list[Any] | None,
    need_types: list[dict[str, str]],
) -> str:
    """next_action：建议下一步（销售看完直接照做）。

    按 grade 排优先级：S 当天 / A 3 天内 / B 培育 / C 跳过。
    销售建议文案复用 recommend.sales_suggestion（规则模板拼接）。
    """
    base = sales_suggestion(
        grade=lead.grade,
        whatsapp_url=lead.whatsapp_url,
        whatsapp_job=bool(lead.whatsapp_job),
        saas_signals=lead.saas_signals,
        has_tier1_contact=any(getattr(c, "seniority", None) == "tier1" for c in (contacts or [])),
        products=products,
    )
    # ICP 门外的线索追加一句（销售不要撞单到 non_buyer/foreign）
    icp = getattr(lead, "icp_status", "unknown") or "unknown"
    if icp in ("foreign", "non_buyer"):
        return f"{base}（注意：ICP 判定为「{ICP_STATUS_LABELS_ZH.get(icp, icp)}」，非出海/非买家，建议人工复核）"
    if icp == "cn_domestic":
        return f"{base}（CN 但未出海证据——可作为长期培育线索）"
    if not need_types:
        return f"{base}（暂未识别明确需求类型，建议先富化官网补信号）"
    return base


def _build_summary(
    grade: str,
    score: int,
    drivers: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    icp_status: str,
) -> str:
    """summary：一句话总结 = 等级 + 分 + 头 3 条证据 + ICP 标签。

    中文模板：「<grade>级线索（<score> 分），主要证据：<top3 drivers>。<ICP>。」
    C 级 + 有 blockers 时换成「…主要缺口：…」叙事，让销售看到改进方向。
    """
    top3 = drivers[:3]
    if grade == "C" and blockers:
        missing_str = "、".join(blockers[0].get("missing", [])[:3]) if blockers else ""
        return (
            f"C级线索（{score} 分，主要缺口：{missing_str}）。"
            f"{ICP_STATUS_LABELS_ZH.get(icp_status, icp_status)}。"
        )
    if top3:
        items_str = "、".join(f"{d['label']}(+{d['points']})" for d in top3)
        return (
            f"{grade}级线索（{score} 分）：{items_str}。"
            f"{ICP_STATUS_LABELS_ZH.get(icp_status, icp_status)}。"
        )
    return (
        f"{grade}级线索（{score} 分）。"
        f"{ICP_STATUS_LABELS_ZH.get(icp_status, icp_status)}。"
    )


# ---------- 缓存键（输入指纹）----------


def compute_cache_key(
    lead: Any,
    contacts: list[Any] | None,
    signals: list[Any] | None,
) -> str:
    """缓存键 = score_signals + signals_top3 + icp_status + contacts_count + grade。

    任一变化 → 失效。任何字段缺失都参与 hash，避免遗漏。

    不含：enriched_at（随时间漂移但不影响解释）、follow_status（CRM 状态）。
    """
    score_signals = json.dumps(
        getattr(lead, "score_signals", None) or {}, sort_keys=True, ensure_ascii=False
    )
    top3_signals = json.dumps(
        [
            (s.signal_type if hasattr(s, "signal_type") else s.get("type"),
             s.value if hasattr(s, "value") else s.get("value"))
            for s in (signals or [])[:3]
        ],
        sort_keys=True,
        ensure_ascii=False,
    )
    payload = "|".join(
        [
            score_signals,
            top3_signals,
            getattr(lead, "icp_status", "unknown") or "unknown",
            getattr(lead, "grade", "C") or "C",
            str(len(contacts or [])),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------- 主函数（模板版）----------


def build_qualify_reason_template(
    lead: Any,
    contacts: list[Any] | None,
    signals: list[Any] | None,
    signal_urls: dict[str, str] | None = None,
) -> dict[str, Any]:
    """纯函数：聚合 score_breakdown + signals + three_questions → JSON 解释。

    销售页展示时直接调用；持久化由 compute_and_save 负责（带缓存 + LLM 增强）。
    """
    contacts = contacts or []
    signals = signals or []
    breakdown = getattr(lead, "score_breakdown", None) or {}
    score_items = sorted(
        breakdown.get("items", []),
        key=lambda it: -int(it.get("points", 0)),
    )
    drivers = _build_drivers(score_items, signals, signal_urls)
    blockers = _build_blockers(lead, contacts)
    products = recommend_products(
        whatsapp_hit=bool(getattr(lead, "whatsapp_hit", False)),
        whatsapp_url=getattr(lead, "whatsapp_url", None),
        whatsapp_job=bool(getattr(lead, "whatsapp_job", False)),
        scenes=list(getattr(lead, "scenes", None) or []),
        saas_signals=dict(getattr(lead, "saas_signals", None) or {}),
        industry=getattr(lead, "industry", None),
        sources=list(getattr(lead, "sources", None) or []),
        whatsapp_numbers=list(getattr(lead, "whatsapp_numbers", None) or []),
        icp_status=getattr(lead, "icp_status", None),
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
    summary = _build_summary(
        grade=getattr(lead, "grade", "C") or "C",
        score=int(getattr(lead, "score", 0) or 0),
        drivers=drivers,
        blockers=blockers,
        icp_status=getattr(lead, "icp_status", "unknown") or "unknown",
    )
    next_action = _build_next_action(lead, products, contacts, need_types)
    cache_key = compute_cache_key(lead, contacts, signals)
    return {
        "summary": summary,
        "drivers": drivers,
        "blockers": blockers,
        "next_action": next_action,
        "generated_by": "template",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_key": cache_key,
    }


# ---------- LLM 增强 ----------


_QUALIFY_SYSTEM = """你是销售判定助手。基于给出的企业画像和命中信号，用一段话（不超过 80 字中文）
解释「为什么是这个等级」。

要求：
- 引用 top 3 证据（带分值）
- 提及 ICP 资格
- 给出明确 next action（销售当天做什么）
- 避免套话（「具有重要意义」「值得关注」之类的）
- 输出 JSON：{"summary": "...", "next_action": "..."}。只输出 JSON。"""


async def build_qualify_reason_with_llm(
    lead: Any,
    contacts: list[Any] | None,
    signals: list[Any] | None,
    signal_urls: dict[str, str] | None,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    """LLM 增强版：summary / next_action 由 LLM 改写，drivers / blockers / cache_key
    沿用模板版（结构稳定，前端展示逻辑不变）。

    失败时原样返回 fallback（generated_by 改为 'template'）。
    """
    if not llm.llm_enabled():
        return fallback
    # LLM 只看结构化片段（避免 prompt 过大）
    drivers = fallback.get("drivers", [])
    blockers = fallback.get("blockers", [])
    user_prompt = (
        f"lead: {lead.name}（{lead.country or '-'}, {lead.industry or '-'}）\n"
        f"grade: {lead.grade}, score: {lead.score}, icp_status: {lead.icp_status}\n"
        f"drivers: {json.dumps(drivers, ensure_ascii=False)}\n"
        f"blockers: {json.dumps(blockers, ensure_ascii=False)}\n"
        f"scenes: {list(getattr(lead, 'scenes', None) or [])}\n"
        f"saas_signals: {dict(getattr(lead, 'saas_signals', None) or {})}\n"
        f"contacts_count: {len(contacts or [])}"
    )
    try:
        result = await llm.chat_json(_QUALIFY_SYSTEM, user_prompt)
    except (BusinessError, Exception) as exc:  # noqa: BLE001
        logger.warning(
            "qualify_reason.llm 降级模板：{}: {}", type(exc).__name__, exc
        )
        return fallback
    new = dict(fallback)
    if isinstance(result.get("summary"), str) and result["summary"].strip():
        new["summary"] = result["summary"].strip()[:200]
    if isinstance(result.get("next_action"), str) and result["next_action"].strip():
        new["next_action"] = result["next_action"].strip()[:300]
    new["generated_by"] = "llm"
    new["generated_at"] = datetime.now(timezone.utc).isoformat()
    return new


# ---------- 持久化（带缓存 + 同步 DB 写入）----------


async def compute_and_save_qualify_reason(
    db: AsyncSession,
    lead: Any,
    contacts: list[Any] | None,
    signals: list[Any] | None,
    signal_urls: dict[str, str] | None = None,
    *,
    use_llm: bool = True,
    force: bool = False,
) -> dict[str, Any] | None:
    """计算 + 写库。命中缓存（输入指纹未变）则直接返回原值。

    force=True 跳过缓存（管理员手动「重新判定」入口用）。
    use_llm=False 跳过 LLM（测试 / 高频批量路径用）。

    返回写入后的 JSON；Lead.qualify_reason 已被修改（但 caller 负责 commit）。
    """
    contacts = contacts or []
    signals = signals or []
    cache_key = compute_cache_key(lead, contacts, signals)
    existing = getattr(lead, "qualify_reason", None)
    if (
        not force
        and isinstance(existing, dict)
        and existing.get("cache_key") == cache_key
        and existing.get("generated_at")
    ):
        return existing
    fallback = build_qualify_reason_template(lead, contacts, signals, signal_urls)
    if use_llm:
        fallback = await build_qualify_reason_with_llm(
            lead, contacts, signals, signal_urls, fallback
        )
    lead.qualify_reason = fallback
    await db.flush()
    return fallback


# AsyncSession 延迟导入（避免循环引用：crud 层依赖 db.session）
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402