"""销售域接口：高价值预警 + AI 能力（分析/话术）+ 数据源管理。

范围口径（PRD §三：V1 明确不做复杂 CRM / 复杂 BI / AI Agent）：
- 商机 CRM（报价/谈判/成交漏斗）、话术审核队列、漏斗/排行榜、自然语言搜索
  已按需求边界移除——线索状态机（follow_status 十态含 won）承担成交回传
- 保留：预警中心（§九 动态事件）、AI 分析/话术（§七 输出规格 + §十一 LLM 层）、
  数据源管理（§一「分析哪个渠道商机产出最高」）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select

from app.api.deps import CurrentUser, SessionDep
from app.api.perms import lead_visible, require_permission, scope_filter_params
from app.collectors import list_collectors
from app.core.exceptions import NotFoundError
from app.models.collect_task import CollectTask
from app.models.lead import Lead, LeadEvent
from app.models.user import User
from app.schemas.collect import (
    AiAnalysisOut,
    LeadEventOut,
    ScriptOut,
)
from app.schemas.common import PageResponse, ResponseModel
from app.services import llm

router = APIRouter()

RequireStatsRead = Depends(require_permission("stats:read"))


async def _get_lead(db: SessionDep, lead_id: int, user: User) -> Lead:
    """取线索并做数据权限校验（own/team 级只能访问自己 + 共享池）。

    越权与不存在同返 404——不向受限用户泄露线索存在性（与 collect 详情口径一致）。
    """
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise NotFoundError("线索不存在")
    scope_owner_ids, _ = await scope_filter_params(db, user)
    if not lead_visible(lead.owner_id, scope_owner_ids):
        raise NotFoundError("线索不存在")
    return lead


def _lead_scope_cond(lead_model, scope_owner_ids: list[int] | None):
    """列表类查询的线索归属过滤（None = 不过滤；否则限定 owner ∈ 范围 + 共享池）。"""
    from sqlalchemy import or_

    if scope_owner_ids is None:
        return None
    return or_(lead_model.owner_id.is_(None), lead_model.owner_id.in_(scope_owner_ids))


async def _user_name_map(db: SessionDep, ids: set[int | None]) -> dict[int | None, str]:
    valid = {i for i in ids if i}
    if not valid:
        return {}
    rows = (await db.execute(select(User.id, User.nickname, User.username).where(User.id.in_(valid)))).all()
    return {r.id: (r.nickname or r.username) for r in rows}


# ---------- 高价值预警（PRD §九/§55） ----------


@router.get(
    "/alerts",
    response_model=ResponseModel[PageResponse[dict]],
    summary="高价值客户预警（发现 WhatsApp / CTWA 代理 / SaaS 信号 / 等级升 S·A）",
)
async def list_alerts(
    db: SessionDep,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    scope_owner_ids, _ = await scope_filter_params(db, user)
    scope_cond = _lead_scope_cond(Lead, scope_owner_ids)
    # ICP 门内才预警（2026-08-31 验证轮：foreign/non_buyer 企业的 WA 发现不该进销售视野）
    icp_cond = Lead.icp_status.notin_(("foreign", "non_buyer"))
    stmt = select(LeadEvent, Lead.name, Lead.grade).join(Lead, LeadEvent.lead_id == Lead.id).where(
        LeadEvent.is_alert, icp_cond
    )
    count_stmt = select(func.count()).select_from(LeadEvent).join(
        Lead, LeadEvent.lead_id == Lead.id
    ).where(LeadEvent.is_alert, icp_cond)
    if scope_cond is not None:
        stmt = stmt.where(scope_cond)
        count_stmt = count_stmt.where(scope_cond)
    total = (await db.execute(count_stmt)).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(LeadEvent.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    items = [
        {
            **LeadEventOut.model_validate(ev).model_dump(),
            "lead_name": name,
            "lead_grade": grade,
        }
        for ev, name, grade in rows
    ]
    return ResponseModel(
        data=PageResponse[dict](items=items, total=total, page=page, page_size=page_size)
    )


# ---------- AI 能力（PRD §七 输出规格 + §十一 三层架构的 LLM 层） ----------


@router.get(
    "/leads/{lead_id}/ai-analysis",
    response_model=ResponseModel[AiAnalysisOut],
    summary="AI 分析客户（企业概况/机会/痛点/推荐/切入点；LLM 未配置降级规则模板）",
)
async def ai_analysis_endpoint(db: SessionDep, user: CurrentUser, lead_id: int):
    lead = await _get_lead(db, lead_id, user)
    from app.crud.contact import list_contacts

    contacts = await list_contacts(db, lead_id)
    result = await llm.ai_analysis(lead, contacts)
    return ResponseModel(data=AiAnalysisOut(**result))


@router.post(
    "/leads/{lead_id}/sales-script",
    response_model=ResponseModel[ScriptOut],
    summary="生成销售话术（§七「AI 话术：已生成」；LLM 未配置降级模板）",
)
async def sales_script_endpoint(db: SessionDep, user: CurrentUser, lead_id: int):
    lead = await _get_lead(db, lead_id, user)
    return ResponseModel(data=ScriptOut(**(await llm.sales_script(lead))))


# ---------- 数据源管理（PRD §一：分析哪个渠道商机产出最高） ----------


@router.get(
    "/data-sources",
    response_model=ResponseModel[list[dict]],
    summary="数据源管理（per-collector 状态/任务数/成功率/数据量/渠道×等级产出）",
)
async def data_sources_endpoint(db: SessionDep, _user: User = RequireStatsRead):
    rows = (
        await db.execute(
            select(
                CollectTask.collector,
                func.count().label("tasks"),
                func.sum(case((CollectTask.status == "failed", 1), else_=0)).label("failed"),
                func.sum(case((CollectTask.status == "completed", 1), else_=0)).label("completed"),
                func.coalesce(func.sum(CollectTask.leads_added), 0).label("added"),
                func.coalesce(func.sum(CollectTask.leads_merged), 0).label("merged"),
                func.max(CollectTask.last_run_at).label("last_run"),
            )
            .where(CollectTask.is_implicit.is_(False))
            .group_by(CollectTask.collector)
        )
    ).all()
    stats_by_collector = {r[0]: r for r in rows}
    # 渠道 × 等级产出（§一：记录来源用于分析哪个渠道商机产出最高）。
    # sources 是 JSON 数组，SQL 拆解方言差异大——按 id 键集分批流式聚合
    grade_by_source: dict[str, dict[str, int]] = {}
    last_id = 0
    while True:
        chunk = (
            await db.execute(
                select(Lead.id, Lead.sources, Lead.grade)
                .where(Lead.id > last_id)
                .order_by(Lead.id)
                .limit(5000)
            )
        ).all()
        if not chunk:
            break
        for _lead_id, sources_json, grade in chunk:
            for rec in sources_json or []:
                name = rec.get("source")
                if name:
                    bucket = grade_by_source.setdefault(name, {"S": 0, "A": 0, "B": 0, "C": 0})
                    if grade in bucket:
                        bucket[grade] += 1
        last_id = chunk[-1][0]
    out = []
    for info in list_collectors():
        # 数据源 = 发现通道（产新线索/新信号）。website_enrich 是内部复核步骤
        # （自动接力 + 每日 cron + 列表勾选富化三入口），不进数据源列表
        # （2026-09-01 用户口径：富化不是数据源，列表里不该出现）
        if info["name"] == "website_enrich":
            continue
        r = stats_by_collector.get(info["name"])
        tasks = r[1] if r else 0
        completed = r[3] if r else 0
        failed = r[2] if r else 0
        done = completed + failed
        out.append(
            {
                "collector": info["name"],
                "title": info["title"],
                "tasks": tasks,
                "success_rate": round(completed * 100 / done) if done else None,
                "error_rate": round(failed * 100 / done) if done else None,
                "leads_added": r[4] if r else 0,
                "leads_merged": r[5] if r else 0,
                "last_run_at": r[6] if r else None,
                "status": "active" if (r and r[6]) else "idle",
                "grade_dist": grade_by_source.get(info["name"], {"S": 0, "A": 0, "B": 0, "C": 0}),
                "logic_note": info.get("logic_note", ""),
            }
        )
    return ResponseModel(data=out)
