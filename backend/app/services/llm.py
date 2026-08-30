"""LLM 集成层（PRD §25/§26/§27/§52）：OpenAI 兼容协议，未配置时降级规则模板。

兼容智谱 GLM（https://open.bigmodel.cn/api/paas/v4）/ DeepSeek / OpenAI 等：
    LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
三个能力：
    ai_analysis    企业概况/WhatsApp 机会/潜在痛点/推荐产品/切入点（§25）
    sales_script   首触话术生成（§26）
    parse_nl_query 自然语言 → 结构化筛选参数（§27/§28）
降级策略：未配置 key 或调用失败 → 抛 LLMNotConfigured / 由调用方回退规则模板，
响应带 generated_by: llm|template 标记，前端明示来源。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from loguru import logger

from app.collectors.recommend import recommend_products, sales_suggestion
from app.collectors.scenes import SAAS_LABELS_ZH, SCENE_LABELS_ZH
from app.collectors.scoring import DIM_LABELS_ZH
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


def _lead_context(lead: Any, dims: dict[str, int], contacts: list[Any]) -> str:
    parts = [
        f"企业：{lead.name}",
        f"行业：{lead.industry or '未知'}；国家：{lead.country or '未知'}；城市：{lead.city or '未知'}",
        f"等级：{lead.grade}（总分 {lead.score}；"
        + "，".join(f"{DIM_LABELS_ZH[k]} {v}" for k, v in dims.items())
        + ")",
        f"WhatsApp：{'已发现 ' + (lead.whatsapp_url or '') if (lead.whatsapp_hit or lead.whatsapp_url) else '未发现'}；"
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


async def ai_analysis(lead: Any, dims: dict[str, int], contacts: list[Any]) -> dict[str, Any]:
    """AI 分析客户（§25）。LLM 不可用时降级为规则模板输出（同结构）。"""
    recs = recommend_products(
        whatsapp_hit=lead.whatsapp_hit,
        whatsapp_url=lead.whatsapp_url,
        whatsapp_job=lead.whatsapp_job,
        scenes=lead.scenes,
        saas_signals=lead.saas_signals,
        industry=lead.industry,
        dim_saas=dims.get("saas", 0),
    )
    fallback = {
        "summary": f"{lead.name}（{lead.industry or '行业未知'}，{lead.country or '地区未知'}），等级 {lead.grade}",
        "whatsapp_opportunity": "已发现 WhatsApp 使用痕迹，可直接以 WhatsApp 建联切入" if (lead.whatsapp_hit or lead.whatsapp_url) else "暂无 WhatsApp 使用证据，建议先富化检测",
        "pain_points": ["海外客服分散在个人号，缺乏统一管理", "客服协作与客户分配无系统支撑", "营销触达缺自动化工具"][: 2 + (1 if lead.whatsapp_job else 0)],
        "products": [{"name": r["name"], "stars": 6 - r["priority"]} for r in recs],
        "entry_point": recs[0]["reason"].split("，")[0] if recs else "先建立联系了解现状",
    }
    if not llm_enabled():
        return {**fallback, "generated_by": "template"}
    try:
        result = await chat_json(_ANALYSIS_SYSTEM, _lead_context(lead, dims, contacts))
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
        whatsapp_job=lead.whatsapp_job,
        scenes=lead.scenes,
        saas_signals=lead.saas_signals,
        industry=lead.industry,
    )
    top = recs[0]["name"] if recs else "WhatsApp 商业化解决方案"
    return (
        f"您好！注意到贵司（{lead.name}）正在服务海外市场"
        f"{'，官网已提供 WhatsApp 入口' if (lead.whatsapp_hit or lead.whatsapp_url) else ''}。"
        f"我们专注帮助出海企业统一管理 WhatsApp 客服与营销触达，{top}已在同行业客户落地。"
        "如果贵司在多客服账号管理、客户分配或营销自动化上有困扰，欢迎约 15 分钟交流，我可以发一份方案给您。"
    )


async def sales_script(lead: Any) -> dict[str, Any]:
    """首触话术（§26）。降级 = 模板拼接。"""
    if not llm_enabled():
        return {"script": _script_fallback(lead), "generated_by": "template"}
    try:
        result = await chat_json(_SCRIPT_SYSTEM, _lead_context(lead, {}, []))
        return {"script": result.get("script") or _script_fallback(lead), "generated_by": "llm"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm.sales_script 降级模板：{}: {}", type(exc).__name__, exc)
        return {"script": _script_fallback(lead), "generated_by": "template"}


# ---------- §27/§28 自然语言搜索 ----------

_NL_SYSTEM = """把销售的自然语言线索筛选请求转成 JSON 查询参数。可用键：
keyword(关键词，企业名/邮箱/域名片段)、country(ISO2 两位码，如 US/MY/SG；中国=CN)、
industry(行业词)、grade(S/A/B/C)、min_score(整数)、whatsapp_hit(true/false)、
is_cn(true/false)、follow_status(unassigned/pending/contacted/replied/opportunity/quote/negotiation/won/invalid/paused)。
推断规则：城市名放 keyword（如"深圳"）；"跨境电商/消费电子"等放 industry；
"美国市场"→country=US；"用WhatsApp"→whatsapp_hit=true；"出海"→is_cn=true；"高分"→min_score=60。
未提及的键不要输出。只输出 JSON。"""


async def parse_nl_query(text: str) -> dict[str, Any]:
    """自然语言 → 结构化筛选（§27）。需要 LLM，未配置抛 LLMNotConfigured。"""
    result = await chat_json(_NL_SYSTEM, text)
    # 白名单过滤，防模型输出非法键
    allowed = {
        "keyword", "country", "industry", "grade", "min_score",
        "whatsapp_hit", "is_cn", "follow_status",
    }
    cleaned = {k: v for k, v in result.items() if k in allowed and v not in (None, "")}
    if isinstance(cleaned.get("country"), str):
        cleaned["country"] = cleaned["country"].upper()[:2]
    if cleaned.get("grade") not in (None, "S", "A", "B", "C"):
        cleaned.pop("grade", None)
    return cleaned


# 规则版建议（详情页旧字段沿用）
def rule_suggestion(lead: Any, dims: dict[str, int], contacts: list[Any]) -> str:
    return sales_suggestion(
        grade=lead.grade,
        whatsapp_url=lead.whatsapp_url,
        whatsapp_job=lead.whatsapp_job,
        saas_signals=lead.saas_signals,
        has_tier1_contact=any(c.seniority == "tier1" for c in contacts),
        products=recommend_products(
            whatsapp_hit=lead.whatsapp_hit,
            whatsapp_url=lead.whatsapp_url,
            whatsapp_job=lead.whatsapp_job,
            scenes=lead.scenes,
            saas_signals=lead.saas_signals,
            industry=lead.industry,
            dim_saas=dims.get("saas", 0),
        ),
    )
