"""线索采集接口：线索列表/录入/删除/批量检测 WhatsApp + 跟进 + 任务管理。

权限：线索与跟进对所有登录用户开放（销售工作台）；
任务管控（建/改/删/执行/取消）与删线索仅管理员。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep, SuperUser
from app.collectors import list_collectors
from app.core.exceptions import BusinessError, NotFoundError
from app.crud.lead import search_leads, upsert_lead
from app.crud.task_crud import list_tasks as query_tasks
from app.crud.task_crud import task_crud
from app.models.collect_task import CollectTask, CollectTaskLog
from app.models.lead import Lead, LeadFollowUp
from app.models.user import User
from app.schemas.collect import (
    FOLLOW_STATUS_OPTIONS,
    CollectorInfo,
    FollowUpCreate,
    FollowUpOut,
    LeadCheckWhatsAppRequest,
    LeadCreate,
    LeadOut,
    TaskCreate,
    TaskLogOut,
    TaskOut,
    TaskUpdate,
)
from app.schemas.common import PageResponse, ResponseModel
from app.services import scheduler as collect_scheduler
from app.services.task_runner import task_runner

router = APIRouter()


async def _user_display_map(db: SessionDep, user_ids: set[int | None]) -> dict[int | None, str]:
    """批量取用户展示名（昵称优先）——列表页注入 owner_name / created_by_name，避免 N+1。"""
    ids = {i for i in user_ids if i}
    if not ids:
        return {}
    rows = (
        await db.execute(select(User.id, User.nickname, User.username).where(User.id.in_(ids)))
    ).all()
    return {r.id: (r.nickname or r.username) for r in rows}


# ---------- 线索 ----------


@router.get("/leads", response_model=ResponseModel[PageResponse[LeadOut]], summary="线索列表")
async def list_leads(
    db: SessionDep,
    _user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    country: str | None = None,
    industry: str | None = None,
    source: str | None = None,
    min_score: int | None = Query(default=None, ge=0),
    whatsapp_hit: bool | None = None,
    has_website: bool | None = None,
    keyword: str | None = None,
    follow_status: str | None = None,
    owner_id: int | None = Query(default=None, ge=1),
    due_follow: bool | None = None,
):
    items, total = await search_leads(
        db,
        page=page,
        page_size=page_size,
        country=country,
        industry=industry,
        source=source,
        min_score=min_score,
        whatsapp_hit=whatsapp_hit,
        has_website=has_website,
        keyword=keyword,
        follow_status=follow_status,
        owner_id=owner_id,
        due_follow=due_follow,
    )
    name_map = await _user_display_map(db, {i.owner_id for i in items})
    outs = []
    for i in items:
        o = LeadOut.model_validate(i)
        o.owner_name = name_map.get(i.owner_id)
        outs.append(o)
    return ResponseModel(
        data=PageResponse[LeadOut](
            items=outs,
            total=total,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/leads", response_model=ResponseModel[LeadOut], summary="手工录入线索")
async def create_lead(db: SessionDep, _user: CurrentUser, payload: LeadCreate):
    from app.collectors.base import LeadDraft

    draft = LeadDraft(
        source=payload.note_source or "manual",
        name=payload.name,
        country=payload.country,
        city=payload.city,
        industry=payload.industry,
        address=payload.address,
        phone_raw=payload.phone,
        website=payload.website,
        email=payload.email,
    )
    lead, _ = await upsert_lead(db, draft)
    await db.commit()
    # 合并路径 UPDATE 后 updated_at（server onupdate）处于 expired 状态，
    # 直接 model_validate 会触发懒加载 IO → MissingGreenlet 422。显式刷新。
    await db.refresh(lead)
    return ResponseModel(data=LeadOut.model_validate(lead))


@router.delete("/leads/{lead_id}", response_model=ResponseModel[None], summary="删除线索")
async def delete_lead(db: SessionDep, _user: SuperUser, lead_id: int):
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise NotFoundError("线索不存在")
    await db.delete(lead)
    await db.commit()
    return ResponseModel()


@router.post(
    "/leads/check-whatsapp",
    response_model=ResponseModel[TaskOut],
    summary="勾选线索 → 创建隐式 website_enrich 任务（复用进度/取消/闸门）",
)
async def check_whatsapp(db: SessionDep, user: CurrentUser, payload: LeadCheckWhatsAppRequest):
    task = await task_crud.create(
        db,
        TaskCreate(
            collector="website_enrich",
            name=f"手动检测 WhatsApp - 选中 {len(payload.lead_ids)} 条",
            params={"lead_ids": payload.lead_ids},
        ).model_dump()
        | {"is_implicit": True, "created_by": user.id},
    )
    await db.commit()
    await task_runner.enqueue(task.id)
    await db.refresh(task)  # enqueue 在独立会话改了 status，刷新再返回
    return ResponseModel(data=TaskOut.model_validate(task))


# ---------- 跟进（销售工作台） ----------


@router.get(
    "/follow-options",
    response_model=ResponseModel[dict],
    summary="跟进弹窗选项（状态词表 + 可选跟进人）",
)
async def follow_options(db: SessionDep, _user: CurrentUser):
    rows = (
        await db.execute(
            select(User.id, User.nickname, User.username)
            .where(User.is_active)
            .order_by(User.id)
        )
    ).all()
    return ResponseModel(
        data={
            "statuses": FOLLOW_STATUS_OPTIONS,
            "users": [{"value": r.id, "label": r.nickname or r.username} for r in rows],
        }
    )


@router.post(
    "/leads/{lead_id}/follow-up",
    response_model=ResponseModel[LeadOut],
    summary="记录跟进：更新线索跟进人/状态/时间并写一条历史",
)
async def create_follow_up(
    db: SessionDep, user: CurrentUser, lead_id: int, payload: FollowUpCreate
):
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise NotFoundError("线索不存在")

    # 指派了跟进人时校验存在（FK 兜底，避免 commit 才炸 IntegrityError）
    owner_id = payload.owner_id or user.id
    if owner_id != user.id and await db.get(User, owner_id) is None:
        raise BusinessError(code=40001, message=f"跟进人不存在：{owner_id}")

    db.add(
        LeadFollowUp(
            lead_id=lead.id,
            user_id=user.id,
            status=payload.status,
            note=payload.note,
            next_follow_at=payload.next_follow_at,
        )
    )
    lead.owner_id = owner_id
    lead.follow_status = payload.status
    lead.last_followed_at = datetime.now(timezone.utc)
    lead.next_follow_at = payload.next_follow_at
    await db.commit()
    await db.refresh(lead)

    out = LeadOut.model_validate(lead)
    out.owner_name = (await _user_display_map(db, {owner_id})).get(owner_id)
    return ResponseModel(data=out)


@router.get(
    "/leads/{lead_id}/follow-ups",
    response_model=ResponseModel[list[FollowUpOut]],
    summary="跟进历史（弹窗时间线，最近 50 条）",
)
async def list_follow_ups(db: SessionDep, _user: CurrentUser, lead_id: int):
    if await db.get(Lead, lead_id) is None:
        raise NotFoundError("线索不存在")
    stmt = (
        select(LeadFollowUp)
        .where(LeadFollowUp.lead_id == lead_id)
        .order_by(LeadFollowUp.id.desc())
        .limit(50)
    )
    items = list((await db.execute(stmt)).scalars().all())
    name_map = await _user_display_map(db, {x.user_id for x in items})
    outs = []
    for x in items:
        o = FollowUpOut.model_validate(x)
        o.user_name = name_map.get(x.user_id)
        outs.append(o)
    return ResponseModel(data=outs)


# ---------- 任务 ----------


@router.get("/collectors", response_model=ResponseModel[list[CollectorInfo]], summary="采集器列表")
async def get_collectors(_user: CurrentUser):
    return ResponseModel(data=[CollectorInfo(**c) for c in list_collectors()])


@router.get("/geo-options", response_model=ResponseModel[dict], summary="国家/城市选项（表单联动数据源）")
async def get_geo_options(_user: CurrentUser):
    from app.collectors.base import CITY_OPTIONS_BY_COUNTRY, COUNTRY_OPTIONS

    return ResponseModel(data={"countries": COUNTRY_OPTIONS, "cities_by_country": CITY_OPTIONS_BY_COUNTRY})


@router.get("/industries", response_model=ResponseModel[list[dict]], summary="线索行业选项（库存 distinct，筛选数据源）")
async def lead_industries(db: SessionDep, _user: CurrentUser):
    """行业筛选下拉的数据源：直接取库里实际存在的 industry 值。

    不用预设词表——google_maps 存关键词、OSM 存标签值、手工录入任意填，
    distinct 才能保证「选项里有的就能查到」。label 是中文展示名
    （OSM 词表映射，未收录原样显示），value 保持原 token 保证筛选精确。
    """
    from sqlalchemy import func

    from app.collectors.osm_overpass import INDUSTRY_LABELS_ZH

    rows = (
        await db.execute(
            select(Lead.industry, func.count())
            .where(Lead.industry.is_not(None), Lead.industry != "")
            .group_by(Lead.industry)
            .order_by(func.count().desc())
        )
    ).all()
    return ResponseModel(
        data=[
            {"label": INDUSTRY_LABELS_ZH.get(name, name), "value": name, "count": cnt}
            for name, cnt in rows
        ]
    )


@router.get("/tasks", response_model=ResponseModel[PageResponse[TaskOut]], summary="任务列表")
async def list_tasks(
    db: SessionDep,
    _user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    collector: str | None = None,
    status: str | None = None,
):
    items, total = await query_tasks(
        db, page=page, page_size=page_size, collector=collector, status=status
    )
    name_map = await _user_display_map(db, {t.created_by for t in items})
    outs = []
    for t in items:
        o = TaskOut.model_validate(t)
        o.created_by_name = name_map.get(t.created_by)
        outs.append(o)
    return ResponseModel(
        data=PageResponse[TaskOut](
            items=outs,
            total=total,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/tasks", response_model=ResponseModel[TaskOut], summary="创建任务")
async def create_task(db: SessionDep, user: SuperUser, payload: TaskCreate):
    from app.collectors import get_collector

    collector = get_collector(payload.collector)
    if collector is None:
        raise BusinessError(code=40001, message=f"未知采集器：{payload.collector}")
    collector.validate_params(payload.params)
    task = await task_crud.create(
        db,
        {
            "name": payload.name or collector.title,
            "collector": payload.collector,
            "params": payload.params,
            "cron_expr": payload.cron_expr,
            "is_implicit": False,
            "created_by": user.id,
        },
    )
    await db.commit()
    if payload.cron_expr:
        await collect_scheduler.sync()
    else:
        await task_runner.enqueue(task.id)
        await db.refresh(task)  # enqueue 在独立会话改了 status，刷新再返回
    return ResponseModel(data=TaskOut.model_validate(task))


@router.get("/tasks/{task_id}", response_model=ResponseModel[TaskOut], summary="任务详情")
async def get_task(db: SessionDep, _user: CurrentUser, task_id: int):
    task = await task_crud.get(db, task_id)
    if task is None:
        raise NotFoundError("任务不存在")
    return ResponseModel(data=TaskOut.model_validate(task))


@router.put("/tasks/{task_id}", response_model=ResponseModel[TaskOut], summary="更新任务")
async def update_task(db: SessionDep, _user: SuperUser, task_id: int, payload: TaskUpdate):
    task = await task_crud.get(db, task_id)
    if task is None:
        raise NotFoundError("任务不存在")
    data = payload.model_dump(exclude_unset=True)
    if "params" in data and data["params"] is not None:
        from app.collectors import get_collector

        collector = get_collector(task.collector)
        if collector is not None:
            collector.validate_params({**(task.params or {}), **data["params"]})
    await task_crud.update(db, task, data)
    if "cron_expr" in data or "enabled" in data:
        await collect_scheduler.sync()
    return ResponseModel(data=TaskOut.model_validate(task))


@router.delete("/tasks/{task_id}", response_model=ResponseModel[None], summary="删除任务")
async def delete_task(db: SessionDep, _user: SuperUser, task_id: int):
    from sqlalchemy import delete as sa_delete

    task = await task_crud.get(db, task_id)
    if task is None:
        raise NotFoundError("任务不存在")
    if task.status == "running":
        raise BusinessError(code=40001, message="任务运行中，请先取消")
    # SQLite 默认不开外键级联，日志显式删（PG 下双保险）
    await db.execute(sa_delete(CollectTaskLog).where(CollectTaskLog.task_id == task_id))
    await db.delete(task)
    await db.commit()
    await collect_scheduler.sync()
    return ResponseModel()


@router.post("/tasks/{task_id}/run", response_model=ResponseModel[None], summary="执行任务")
async def run_task(db: SessionDep, _user: SuperUser, task_id: int):
    task = await task_crud.get(db, task_id)
    if task is None:
        raise NotFoundError("任务不存在")
    await task_runner.enqueue(task_id)
    return ResponseModel()


@router.post("/tasks/{task_id}/cancel", response_model=ResponseModel[None], summary="取消任务")
async def cancel_task(db: SessionDep, _user: SuperUser, task_id: int):
    task = await task_crud.get(db, task_id)
    if task is None:
        raise NotFoundError("任务不存在")
    ok = await task_runner.cancel(task_id)
    if not ok:
        raise BusinessError(code=40001, message=f"任务状态 {task.status}，无需取消")
    return ResponseModel()


@router.get(
    "/tasks/{task_id}/logs",
    response_model=ResponseModel[PageResponse[TaskLogOut]],
    summary="任务日志（after_id 增量轮询）",
)
async def get_task_logs(
    db: SessionDep,
    _user: CurrentUser,
    task_id: int,
    after_id: int = Query(default=0, ge=0),
    page_size: int = Query(default=100, ge=1, le=500),
):
    stmt = (
        select(CollectTaskLog)
        .where(CollectTaskLog.task_id == task_id, CollectTaskLog.id > after_id)
        .order_by(CollectTaskLog.id)
        .limit(page_size)
    )
    items = list((await db.execute(stmt)).scalars().all())
    return ResponseModel(
        data=PageResponse[TaskLogOut](
            items=[TaskLogOut.model_validate(x) for x in items],
            total=len(items),
            page=1,
            page_size=page_size,
        )
    )


# ---------- 汇总（任务列表页快捷统计，避免前端拼多个请求）----------
@router.get("/stats", response_model=ResponseModel[dict], summary="采集总览统计")
async def collect_stats(db: SessionDep, _user: CurrentUser):
    from sqlalchemy import func

    total = (await db.execute(select(func.count()).select_from(Lead))).scalar_one()
    whatsapp = (
        await db.execute(select(func.count()).select_from(Lead).where(Lead.whatsapp_hit))
    ).scalar_one()
    high_intent = (
        await db.execute(select(func.count()).select_from(Lead).where(Lead.score >= 40))
    ).scalar_one()
    running = (
        await db.execute(
            select(func.count())
            .select_from(CollectTask)
            .where(CollectTask.status.in_(["queued", "running"]))
        )
    ).scalar_one()
    # 跟进维度：从未跟进的（共享池待认领）+ 约定回访时间已到期的
    pending_follow = (
        await db.execute(
            select(func.count()).select_from(Lead).where(Lead.follow_status.is_(None))
        )
    ).scalar_one()
    due_follow = (
        await db.execute(
            select(func.count()).select_from(Lead).where(Lead.next_follow_at <= func.now())
        )
    ).scalar_one()
    return ResponseModel(
        data={
            "total_leads": total,
            "whatsapp_leads": whatsapp,
            "high_intent_leads": high_intent,
            "active_tasks": running,
            "pending_leads": pending_follow,
            "due_follow_leads": due_follow,
        }
    )
