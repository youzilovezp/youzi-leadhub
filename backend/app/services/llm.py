"""LLM 集成层（PRD §25/§26/§27/§52）：OpenAI 兼容协议，未配置时降级规则模板。

兼容智谱 GLM（https://open.bigmodel.cn/api/paas/v4）/ DeepSeek / OpenAI 等：
    LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
两个能力（PRD §七 输出规格 + §十一 三层架构的 LLM 层；NL 搜索已按需求边界移除）：
    ai_analysis    企业概况/WhatsApp 机会/潜在痛点/推荐产品/切入点（§25）
    sales_script   首触话术生成（§26）
降级策略：未配置 key 或调用失败 → 抛 LLMNotConfigured / 由调用方回退规则模板，
响应带 generated_by: llm|template 标记，前端明示来源。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from loguru import logger

from app.collectors.recommend import recommend_products
from app.collectors.scenes import SAAS_LABELS_ZH, SCENE_LABELS_ZH
from app.collectors.scoring import INTENT_LABELS_ZH
from app.core.config import settings


class LLMNotConfiguredError(Exception):
    """未配置 LLM 凭据——调用方应降级到规则模板。"""


def llm_enabled() -> bool:
    return bool(settings.LLM_BASE_URL and settings.LLM_API_KEY)


async def chat_json(system: str, user: str) -> dict[str, Any]:
    """调用 chat/completions 并解析 JSON 输出。失败抛异常，由调用方决定降级。"""
    if not llm_enabled():
        raise LLMNotConfiguredError("未配置 LLM_BASE_URL / LLM_API_KEY")
    payload = {
        "model": settings.LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
    }
    async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client:
        resp = await client.post(
            f"{settings.LLM_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
            json=payload,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    # 模型可能包 ```json 围栏，剥掉再解析
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(text)


# ---------- §25 AI 分析客户 ----------

_ANALYSIS_SYSTEM = """你是 WhatsApp Business API 产品的销售分析师。基于给出的企业画像数据，
输出 JSON（中文值）：{"summary": 企业概况一句话, "whatsapp_opportunity": WhatsApp 切入机会,
"pain_points": [潜在痛点，最多4条], "products": [{"name": 产品名, "stars": 1-5}],
"entry_point": 建议销售切入点一句话}。只输出 JSON。"""


def _lead_context(lead: Any, contacts: list[Any]) -> str:
    parts = [
        f"企业：{lead.name}",
        f"行业：{lead.industry or '未知'}；国家：{lead.country or '未知'}；城市：{lead.city or '未知'}",
        f"等级：{lead.grade}（意向分 {lead.score}；"
        + (
            "，".join(
                f"{INTENT_LABELS_ZH.get(k, k)} {v}" for k, v in (lead.score_signals or {}).items()
            )
            or "暂未检测到意向信号"
        )
        + "）",
        f"WhatsApp：{'已发现 ' + (lead.whatsapp_url or '') if (lead.whatsapp_hit or lead.whatsapp_url) else '未发现'}"
        + (
            f"（另有号码 {'、'.join((getattr(lead, 'whatsapp_numbers', None) or [])[:5])}）"
            if (getattr(lead, "whatsapp_numbers", None) or [])
            else ""
        )
        + "；"
        f"FB 私域：{'是' if lead.fb_whatsapp else '否'}；在招 WA 岗位：{'是' if lead.whatsapp_job else '否'}",
        "场景："
        + ("、".join(SCENE_LABELS_ZH.get(s) or s for s in (lead.scenes or [])) or "未检测"),
        "SaaS 信号："
        + ("、".join(SAAS_LABELS_ZH.get(k) or k for k in (lead.saas_signals or {})) or "无"),
        f"官网：{lead.website or '无'}；邮箱：{lead.email or '无'}；社媒：{','.join((lead.social or {}).keys()) or '无'}",
        "联系人："
        + (
            "、".join(f"{c.name or c.email}({c.job_title or '职位待补全'})" for c in contacts[:5])
            or "暂无"
        ),
    ]
    return "\n".join(parts)


async def ai_analysis(lead: Any, contacts: list[Any]) -> dict[str, Any]:
    """AI 分析客户（§25）。LLM 不可用时降级为规则模板输出（同结构）。"""
    recs = recommend_products(
        whatsapp_hit=lead.whatsapp_hit,
        whatsapp_url=lead.whatsapp_url,
        whatsapp_numbers=list(getattr(lead, "whatsapp_numbers", None) or []),
        whatsapp_job=lead.whatsapp_job,
        scenes=lead.scenes,
        saas_signals=lead.saas_signals,
        industry=lead.industry,
        sources=lead.sources,
    )
    uses_wa = bool(
        lead.whatsapp_hit or lead.whatsapp_url or (getattr(lead, "whatsapp_numbers", None) or [])
    )
    fallback = {
        "summary": f"{lead.name}（{lead.industry or '行业未知'}，{lead.country or '地区未知'}），等级 {lead.grade}",
        "whatsapp_opportunity": "已发现 WhatsApp 使用痕迹，可直接以 WhatsApp 建联切入"
        if uses_wa
        else "暂无 WhatsApp 使用证据，建议先富化检测",
        "pain_points": [
            "海外客服分散在个人号，缺乏统一管理",
            "客服协作与客户分配无系统支撑",
            "营销触达缺自动化工具",
        ][: 2 + (1 if lead.whatsapp_job else 0)],
        "products": [{"name": r["name"], "stars": 6 - r["priority"]} for r in recs],
        "entry_point": recs[0]["reason"].split("，")[0] if recs else "先建立联系了解现状",
    }
    if not llm_enabled():
        return {**fallback, "generated_by": "template"}
    try:
        result = await chat_json(_ANALYSIS_SYSTEM, _lead_context(lead, contacts))
        return {**fallback, **result, "generated_by": "llm"}
    except Exception as exc:  # noqa: BLE001  LLM 失败不挡业务，降级模板
        logger.warning("llm.ai_analysis 降级模板：{}: {}", type(exc).__name__, exc)
        return {**fallback, "generated_by": "template"}


# ---------- §26 话术生成 ----------

_SCRIPT_SYSTEM = """你是 WhatsApp Business API / SaaS 产品的销售。基于企业画像写一段首次建联的
中文话术（150字内）：点出对方业务与 WhatsApp 使用现状 → 我们的价值主张 → 一个轻量的行动请求。
输出 JSON：{"script": "话术全文"}。只输出 JSON。"""


def _script_fallback(lead: Any) -> str:
    recs = recommend_products(
        whatsapp_hit=lead.whatsapp_hit,
        whatsapp_url=lead.whatsapp_url,
        whatsapp_numbers=list(getattr(lead, "whatsapp_numbers", None) or []),
        whatsapp_job=lead.whatsapp_job,
        scenes=lead.scenes,
        saas_signals=lead.saas_signals,
        industry=lead.industry,
        sources=lead.sources,
    )
    top = recs[0]["name"] if recs else "WhatsApp 商业化解决方案"
    uses_wa = bool(
        lead.whatsapp_hit or lead.whatsapp_url or (getattr(lead, "whatsapp_numbers", None) or [])
    )
    return (
        f"您好！注意到贵司（{lead.name}）正在服务海外市场"
        f"{'，已提供 WhatsApp 联系入口' if uses_wa else ''}。"
        f"我们专注帮助出海企业统一管理 WhatsApp 客服与营销触达，{top}已在同行业客户落地。"
        "如果贵司在多客服账号管理、客户分配或营销自动化上有困扰，欢迎约 15 分钟交流，我可以发一份方案给您。"
    )


async def sales_script(lead: Any) -> dict[str, Any]:
    """首触话术（§26）。降级 = 模板拼接。"""
    if not llm_enabled():
        return {"script": _script_fallback(lead), "generated_by": "template"}
    try:
        result = await chat_json(_SCRIPT_SYSTEM, _lead_context(lead, []))
        return {"script": result.get("script") or _script_fallback(lead), "generated_by": "llm"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm.sales_script 降级模板：{}: {}", type(exc).__name__, exc)
        return {"script": _script_fallback(lead), "generated_by": "template"}
