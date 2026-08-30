"""销售域接口：商机（轻量 CRM）+ 话术审核队列 + 高价值预警 + AI 能力 + 漏斗/排行/数据源。

权限（§42/§43）：
- 商机/话术/AI/预警：登录用户 + 数据权限（own/team 级只能操作可见线索）
- 漏斗/排行/数据源：stats:read 权限码（主管/运营/数据管理员）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select

from app.api.deps import CurrentUser, SessionDep
from app.api.perms import lead_visible, require_permission, scope_filter_params
from app.collectors import list_collectors
from app.core.exceptions import BusinessError, NotFoundError
from app.crud.lead_events import describe_dimensions
from app.crud.opportunity import (
    OPPORTUNITY_STAGES,
    create_opportunity,
    delete_opportunity,
    funnel_stats,
    get_message,
    get_opportunity,
    list_opportunities,
    review_message,
    update_opportunity,
)
from app.models.collect_task import CollectTask
from app.models.lead import Lead, LeadEvent
from app.models.sales import Opportunity, SalesMessage
from app.models.user import User
from app.schemas.collect import (
    AiAnalysisOut,
    LeadEventOut,
    MessageOut,
    MessageReviewPayload,
    NlSearchRequest,
    OpportunityCreate,
    OpportunityOut,
    OpportunityUpdate,
    ScriptOut,
)
from app.schemas.common import PageResponse, ResponseModel
from app.services import llm

router = APIRouter()

RequireLeadRead = Depends(require_permission("lead:read"))
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


# ---------- 商机（PRD §37） ----------


@router.get(
    "/leads/{lead_id}/opportunities",
    response_model=ResponseModel[list[OpportunityOut]],
    summary="商机列表",
)
async def list_lead_opportunities(db: SessionDep, user: CurrentUser, lead_id: int):
    await _get_lead(db, lead_id, user)
    opps = await list_opportunities(db, lead_id)
    name_map = await _user_name_map(db, {o.owner_id for o in opps})
    outs = []
    for o in opps:
        out = OpportunityOut.model_validate(o)
        out.owner_name = name_map.get(o.owner_id)
        outs.append(out)
    return ResponseModel(data=outs)


@router.post(
    "/leads/{lead_id}/opportunities",
    response_model=ResponseModel[OpportunityOut],
    summary="新增商机（线索状态联动推进到有效商机）",
)
async def create_lead_opportunity(
    db: SessionDep, user: CurrentUser, lead_id: int, payload: OpportunityCreate
):
    lead = await _get_lead(db, lead_id, user)
    opp = await create_opportunity(db, lead, payload, owner_id=lead.owner_id or user.id)
    await db.commit()
    await db.refresh(opp)
    out = OpportunityOut.model_validate(opp)
    out.owner_name = (await _user_name_map(db, {opp.owner_id})).get(opp.owner_id)
    return ResponseModel(data=out)


@router.put(
    "/leads/{lead_id}/opportunities/{opp_id}",
    response_model=ResponseModel[OpportunityOut],
    summary="推进商机阶段/改金额（成交联动线索状态）",
)
async def update_lead_opportunity(
    db: SessionDep,
    user: CurrentUser,
    lead_id: int,
    opp_id: int,
    payload: OpportunityUpdate,
):
    lead = await _get_lead(db, lead_id, user)
    opp = await get_opportunity(db, lead_id, opp_id)
    if opp is None:
        raise NotFoundError("商机不存在")
    opp = await update_opportunity(db, lead, opp, payload)
    await db.commit()
    await db.refresh(opp)
    return ResponseModel(data=OpportunityOut.model_validate(opp))


@router.delete(
    "/leads/{lead_id}/opportunities/{opp_id}",
    response_model=ResponseModel[None],
    summary="删除商机",
)
async def delete_lead_opportunity(
    db: SessionDep, user: CurrentUser, lead_id: int, opp_id: int
):
    lead = await _get_lead(db, lead_id, user)
    opp = await get_opportunity(db, lead_id, opp_id)
    if opp is None:
        raise NotFoundError("商机不存在")
    await delete_opportunity(db, lead, opp)
    await db.commit()
    return ResponseModel()


@router.get("/stage-options", response_model=ResponseModel[dict], summary="商机阶段词表")
async def stage_options(_user: CurrentUser):
    return ResponseModel(data={"stages": OPPORTUNITY_STAGES})


# ---------- 话术审核队列（PRD §56） ----------


async def _user_name_map(db: SessionDep, ids: set[int | None]) -> dict[int | None, str]:
    valid = {i for i in ids if i}
    if not valid:
        return {}
    rows = (await db.execute(select(User.id, User.nickname, User.username).where(User.id.in_(valid)))).all()
    return {r.id: (r.nickname or r.username) for r in rows}


@router.get(
    "/messages",
    response_model=ResponseModel[PageResponse[MessageOut]],
    summary="话术队列（生成 → 审核 → 发送）",
)
async def list_messages(
    db: SessionDep,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = None,
    lead_id: int | None = Query(default=None, ge=1),
):
    conds = []
    if status:
        conds.append(SalesMessage.status == status)
    if lead_id:
        conds.append(SalesMessage.lead_id == lead_id)
    # 数据权限（§43）：own/team 级只能看可见线索的话术（join Lead 限定归属）
    scope_owner_ids, _ = await scope_filter_params(db, user)
    scope_cond = _lead_scope_cond(Lead, scope_owner_ids)

    def _apply(st):
        for c in conds:
            st = st.where(c)
        if scope_cond is not None:
            st = st.join(Lead, Lead.id == SalesMessage.lead_id).where(scope_cond)
        return st

    stmt = _apply(select(SalesMessage))
    count_stmt = _apply(select(func.count()).select_from(SalesMessage))
    total = (await db.execute(count_stmt)).scalar_one()
    items = list(
        (
            await db.execute(
                stmt.order_by(SalesMessage.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    lead_names = {
        r.id: r.name
        for r in (
            await db.execute(select(Lead.id, Lead.name).where(Lead.id.in_({m.lead_id for m in items})))
        ).all()
    }
    outs = []
    for m in items:
        o = MessageOut.model_validate(m)
        o.lead_name = lead_names.get(m.lead_id)
        outs.append(o)
    return ResponseModel(
        data=PageResponse[MessageOut](items=outs, total=total, page=page, page_size=page_size)
    )


@router.post(
    "/leads/{lead_id}/messages/generate",
    response_model=ResponseModel[MessageOut],
    summary="生成首触话术（LLM，未配置自动降级模板）并进入待审核队列",
)
async def generate_message(db: SessionDep, user: CurrentUser, lead_id: int):
    from app.crud.opportunity import create_message

    lead = await _get_lead(db, lead_id, user)
    result = await llm.sales_script(lead)
    msg = await create_message(
        db, lead, result["script"], generated_by=result["generated_by"], created_by=user.id
    )
    await db.commit()
    await db.refresh(msg)
    out = MessageOut.model_validate(msg)
    out.lead_name = lead.name
    return ResponseModel(data=out)


@router.post(
    "/messages/{message_id}/review",
    response_model=ResponseModel[MessageOut],
    summary="审核话术（approve/reject）或标记已发送（mark_sent，人工复制发送后回填）",
)
async def review_message_endpoint(
    db: SessionDep, user: CurrentUser, message_id: int, payload: MessageReviewPayload
):
    msg = await get_message(db, message_id)
    if msg is None:
        raise NotFoundError("话术不存在")
    if msg.lead_id:
        # 数据权限：只能审核可见线索的话术
        await _get_lead(db, msg.lead_id, user)
    msg = await review_message(db, msg, payload.action, reviewer_id=user.id)
    await db.commit()
    await db.refresh(msg)
    out = MessageOut.model_validate(msg)
    if msg.lead_id:
        lead_row = await db.get(Lead, msg.lead_id)
        out.lead_name = lead_row.name if lead_row else None
    return ResponseModel(data=out)


# ---------- 高价值预警（PRD §55） ----------


@router.get(
    "/alerts",
    response_model=ResponseModel[PageResponse[dict]],
    summary="高价值客户预警（发现 WhatsApp / SaaS 信号 / 等级升 S·A）",
)
async def list_alerts(
    db: SessionDep,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    scope_owner_ids, _ = await scope_filter_params(db, user)
    scope_cond = _lead_scope_cond(Lead, scope_owner_ids)
    stmt = select(LeadEvent, Lead.name, Lead.grade).join(Lead, LeadEvent.lead_id == Lead.id).where(
        LeadEvent.is_alert
    )
    count_stmt = select(func.count()).select_from(LeadEvent).join(
        Lead, LeadEvent.lead_id == Lead.id
    ).where(LeadEvent.is_alert)
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


# ---------- AI 能力（PRD §25/§26/§27） ----------


@router.get(
    "/leads/{lead_id}/ai-analysis",
    response_model=ResponseModel[AiAnalysisOut],
    summary="AI 分析客户（企业概况/机会/痛点/推荐/切入点；LLM 未配置降级规则模板）",
)
async def ai_analysis_endpoint(db: SessionDep, user: CurrentUser, lead_id: int):
    lead = await _get_lead(db, lead_id, user)
    from app.crud.contact import list_contacts

    contacts = await list_contacts(db, lead_id)
    dims = describe_dimensions(lead.score_signals)
    result = await llm.ai_analysis(lead, dims, contacts)
    return ResponseModel(data=AiAnalysisOut(**result))


@router.post(
    "/leads/{lead_id}/sales-script",
    response_model=ResponseModel[ScriptOut],
    summary="生成销售话术（不落库，预览用；入队列走 messages/generate）",
)
async def sales_script_endpoint(db: SessionDep, user: CurrentUser, lead_id: int):
    lead = await _get_lead(db, lead_id, user)
    return ResponseModel(data=ScriptOut(**(await llm.sales_script(lead))))


@router.post(
    "/leads/search-nl",
    response_model=ResponseModel[dict],
    summary="自然语言 → 结构化筛选参数（需配置 LLM）",
)
async def search_nl(
    payload: NlSearchRequest,
    _user: User = RequireLeadRead,
):
    if not llm.llm_enabled():
        raise BusinessError(
            code=40001,
            message="未配置 LLM（LLM_BASE_URL / LLM_API_KEY），自然语言搜索不可用；可在 .env 配置后重启",
        )
    try:
        params = await llm.parse_nl_query(payload.text)
    except Exception as exc:  # noqa: BLE001  LLM 网络失败给可行动提示
        raise BusinessError(code=50000, message=f"自然语言解析失败：{exc}") from exc
    return ResponseModel(data={"params": params})


# ---------- 漏斗 / 排行榜 / 数据源（PRD §38/§39/§40/§33） ----------


@router.get("/funnel", response_model=ResponseModel[dict], summary="销售漏斗（各阶段线索数 + 商机金额口径）")
async def funnel_endpoint(db: SessionDep, _user: User = RequireStatsRead):
    return ResponseModel(data=await funnel_stats(db))


@router.get(
    "/leaderboard",
    response_model=ResponseModel[list[dict]],
    summary="销售排行榜（Lead 数 / 商机数 / 成交数 / 成交金额，PRD §40）",
)
async def leaderboard_endpoint(db: SessionDep, _user: User = RequireStatsRead):
    # 商机口径排行（opportunities 有 owner 与金额）
    rows = (
        await db.execute(
            select(
                Opportunity.owner_id,
                func.count().label("opportunities"),
                func.sum(case((Opportunity.stage == "won", 1), else_=0)).label("won"),
                func.coalesce(
                    func.sum(case((Opportunity.stage == "won", Opportunity.amount), else_=0)), 0
                ).label("won_amount"),
            )
            .where(Opportunity.owner_id.is_not(None))
            .group_by(Opportunity.owner_id)
        )
    ).all()
    owner_ids = {r[0] for r in rows}
    # 线索持有量
    lead_rows = (
        await db.execute(
            select(Lead.owner_id, func.count())
            .where(Lead.owner_id.is_not(None))
            .group_by(Lead.owner_id)
        )
    ).all()
    lead_counts = {r[0]: r[1] for r in lead_rows}
    owner_ids |= set(lead_counts)
    names = await _user_name_map(db, owner_ids)
    board = []
    for uid in owner_ids:
        opp = next((r for r in rows if r[0] == uid), None)
        board.append(
            {
                "owner_id": uid,
                "owner_name": names.get(uid),
                "leads": lead_counts.get(uid, 0),
                "opportunities": opp[1] if opp else 0,
                "won": int(opp[2]) if opp else 0,
                "won_amount": int(opp[3]) if opp else 0,
            }
        )
    board.sort(key=lambda x: (-x["won_amount"], -x["won"], -x["leads"]))
    return ResponseModel(data=board)


@router.get(
    "/data-sources",
    response_model=ResponseModel[list[dict]],
    summary="数据源管理（per-collector 状态/任务数/成功率/数据量/最后运行，从任务表聚合）",
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
    # 渠道 × 等级产出（补充需求 §一：记录来源用于分析哪个渠道商机产出最高）。
    # sources 是 JSON 数组，SQL 拆解方言差异大——Python 侧聚合最稳
    leads_src_grade = (await db.execute(select(Lead.sources, Lead.grade))).all()
    grade_by_source: dict[str, dict[str, int]] = {}
    for sources_json, grade in leads_src_grade:
        for rec in sources_json or []:
            name = rec.get("source")
            if name:
                bucket = grade_by_source.setdefault(name, {"S": 0, "A": 0, "B": 0, "C": 0})
                if grade in bucket:
                    bucket[grade] += 1
    out = []
    for info in list_collectors():
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
            }
        )
    return ResponseModel(data=out)
