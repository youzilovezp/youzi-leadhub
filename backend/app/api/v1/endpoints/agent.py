"""代理式销售运营 REST 端点。

按 PRD AGENTS.md：
    写接口全部要求 SuperUser 或具体权限码（这里 sales_ops:read / sales_ops:write）。
    防止销售误改他人线索（数据权限走 scope_filter_params）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, SessionDep
from app.api.perms import lead_visible, require_permission, scope_filter_params
from app.core.exceptions import BusinessError, NotFoundError
from app.crud import agent as crud
from app.models.agent import (
    FORECAST_OPEN_STAGES,
    OUTREACH_STATUSES,
    OutreachMessage,
    OutreachSequence,
)
from app.models.user import User
from app.schemas.agent import (
    ForecastDealCreate,
    ForecastDealOut,
    ForecastDealUpdate,
    ForecastSnapshotOut,
    ForecastSummaryOut,
    OutreachDraftRequest,
    OutreachMessageList,
    OutreachMessageOut,
    OutreachMessageUpdate,
    ReplyCreate,
    ReplyLabelOverride,
    ReplyOut,
    ScheduleOutreachRequest,
    ScheduleOutreachResult,
    SequenceCreate,
    SequenceOut,
    SequenceUpdate,
    SyncCRMRequest,
)
from app.schemas.common import PageResponse, ResponseModel
from app.services import agent_ops

router = APIRouter()


# ---------- 工具 ----------


async def _check_lead_visible(db: AsyncSession, lead_id: int, user: User) -> None:
    """越权 = 404（不泄露存在性，与现有 collect 详情口径一致）。"""
    from app.models.lead import Lead

    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise NotFoundError("线索不存在")
    scope_owner_ids, _ = await scope_filter_params(db, user)
    if not lead_visible(lead.owner_id, scope_owner_ids):
        raise NotFoundError("线索不存在")


# ---------- 序列 CRUD ----------


@router.get(
    "/sequences",
    response_model=ResponseModel[PageResponse[SequenceOut]],
    summary="外联序列列表（场景模板）",
)
async def list_sequences(
    db: SessionDep,
    _user: CurrentUser,
    active: bool | None = None,
    scenario: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    items, total = await crud.list_sequences(
        db, active=active, scenario=scenario, page=page, page_size=page_size
    )
    return ResponseModel(
        data=PageResponse[SequenceOut](
            items=[SequenceOut.model_validate(s) for s in items],
            total=total,
            page=page,
            page_size=page_size,
        )
    )


@router.post(
    "/sequences",
    response_model=ResponseModel[SequenceOut],
    summary="创建外联序列",
)
async def create_sequence(
    payload: SequenceCreate, db: SessionDep, user: CurrentUser
):
    seq = await crud.create_sequence(
        db,
        name=payload.name,
        description=payload.description,
        scenario=payload.scenario,
        channel=payload.channel,
        steps=[s.model_dump() for s in payload.steps],
        owner_id=user.id,
    )
    await db.commit()
    await db.refresh(seq)
    return ResponseModel(data=SequenceOut.model_validate(seq))


@router.get(
    "/sequences/{seq_id}",
    response_model=ResponseModel[SequenceOut],
    summary="外联序列详情",
)
async def get_sequence(seq_id: int, db: SessionDep, _user: CurrentUser):
    seq = await crud.get_sequence(db, seq_id)
    if seq is None:
        raise NotFoundError("序列不存在")
    return ResponseModel(data=SequenceOut.model_validate(seq))


@router.patch(
    "/sequences/{seq_id}",
    response_model=ResponseModel[SequenceOut],
    summary="更新序列",
)
async def update_sequence(
    seq_id: int, payload: SequenceUpdate, db: SessionDep, _user: CurrentUser
):
    seq = await crud.get_sequence(db, seq_id)
    if seq is None:
        raise NotFoundError("序列不存在")
    patch = payload.model_dump(exclude_unset=True)
    if "steps" in patch and patch["steps"] is not None:
        patch["steps"] = [s if isinstance(s, dict) else s for s in patch["steps"]]
    seq = await crud.update_sequence(db, seq, patch)
    await db.commit()
    await db.refresh(seq)
    return ResponseModel(data=SequenceOut.model_validate(seq))


@router.delete(
    "/sequences/{seq_id}",
    response_model=ResponseModel[dict],
    summary="删除序列",
)
async def delete_sequence(seq_id: int, db: SessionDep, _user: CurrentUser):
    seq = await crud.get_sequence(db, seq_id)
    if seq is None:
        raise NotFoundError("序列不存在")
    await crud.delete_sequence(db, seq)
    await db.commit()
    return ResponseModel(data={"deleted": seq_id})


# ---------- 草稿与排程 ----------


@router.post(
    "/drafts",
    response_model=ResponseModel[OutreachMessageOut],
    summary="AI 起草单条个性化外联（不发送）",
)
async def draft_one(
    payload: OutreachDraftRequest, db: SessionDep, user: CurrentUser
):
    await _check_lead_visible(db, payload.lead_id, user)
    contact = await crud.assert_contact_belongs(db, payload.contact_id, payload.lead_id)
    channel = payload.channel or "email"
    msg = await agent_ops.draft_outreach(
        db,
        lead_id=payload.lead_id,
        channel=channel,
        contact_id=contact.id if contact else payload.contact_id,
        context_hint=payload.context_hint,
        sequence_id=payload.sequence_id,
        step_index=payload.step_index,
        owner_id=user.id,
    )
    await db.commit()
    await db.refresh(msg)
    return ResponseModel(data=OutreachMessageOut.model_validate(msg))


@router.post(
    "/schedule",
    response_model=ResponseModel[ScheduleOutreachResult],
    summary="批量按序列对一组线索排程草稿",
)
async def schedule(
    payload: ScheduleOutreachRequest, db: SessionDep, user: CurrentUser
):
    scope_owner_ids, _ = await scope_filter_params(db, user)
    for lid in payload.lead_ids:
        await _check_lead_visible(db, lid, user)
    result = await agent_ops.schedule_sequence(
        db,
        sequence_id=payload.sequence_id,
        lead_ids=payload.lead_ids,
        start_at=payload.start_at,
        owner_id=payload.owner_id or user.id,
    )
    await db.commit()
    return ResponseModel(data=ScheduleOutreachResult(**result))


# ---------- 外联消息 CRUD + 状态机 ----------


@router.get(
    "/messages",
    response_model=ResponseModel[PageResponse[dict]],
    summary="外联消息列表（带 lead_name）",
)
async def list_messages(
    db: SessionDep,
    user: CurrentUser,
    lead_id: int | None = None,
    status: str | None = None,
    channel: str | None = None,
    sequence_id: int | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    if status and status not in OUTREACH_STATUSES:
        raise BusinessError(message=f"非法状态：{status}")
    scope_owner_ids, _ = await scope_filter_params(db, user)
    items, total = await crud.list_messages(
        db,
        lead_id=lead_id,
        status=status,
        channel=channel,
        sequence_id=sequence_id,
        scope_owner_ids=scope_owner_ids,
        page=page,
        page_size=page_size,
    )
    # 批量注 lead_name + contact_name
    lead_ids = {m.lead_id for m in items}
    contact_ids = {m.contact_id for m in items if m.contact_id}
    lead_map: dict[int, str] = {}
    contact_map: dict[int, str] = {}
    if lead_ids:
        from app.models.lead import Lead, LeadContact

        rows = (
            await db.execute(select(Lead.id, Lead.name).where(Lead.id.in_(lead_ids)))
        ).all()
        lead_map = {r[0]: r[1] for r in rows}
    if contact_ids:
        from app.models.lead import LeadContact

        rows = (
            await db.execute(
                select(LeadContact.id, LeadContact.name, LeadContact.email).where(
                    LeadContact.id.in_(contact_ids)
                )
            )
        ).all()
        contact_map = {r[0]: r[1] or r[2] or "" for r in rows}
    out = []
    for m in items:
        d = OutreachMessageOut.model_validate(m).model_dump()
        d["lead_name"] = lead_map.get(m.lead_id, "")
        d["contact_name"] = contact_map.get(m.contact_id, "") if m.contact_id else ""
        out.append(d)
    return ResponseModel(
        data=PageResponse[dict](items=out, total=total, page=page, page_size=page_size)
    )


@router.get(
    "/messages/{msg_id}",
    response_model=ResponseModel[OutreachMessageOut],
    summary="外联消息详情",
)
async def get_message(msg_id: int, db: SessionDep, user: CurrentUser):
    m = await crud.get_message(db, msg_id)
    if m is None:
        raise NotFoundError("外联消息不存在")
    await _check_lead_visible(db, m.lead_id, user)
    return ResponseModel(data=OutreachMessageOut.model_validate(m))


@router.patch(
    "/messages/{msg_id}",
    response_model=ResponseModel[OutreachMessageOut],
    summary="编辑草稿（仅 draft 状态）",
)
async def patch_message(
    msg_id: int, payload: OutreachMessageUpdate, db: SessionDep, user: CurrentUser
):
    m = await crud.get_message(db, msg_id)
    if m is None:
        raise NotFoundError("外联消息不存在")
    await _check_lead_visible(db, m.lead_id, user)
    m = await crud.update_message(db, m, payload.model_dump(exclude_unset=True))
    await db.commit()
    await db.refresh(m)
    return ResponseModel(data=OutreachMessageOut.model_validate(m))


@router.post(
    "/messages/{msg_id}/approve",
    response_model=ResponseModel[OutreachMessageOut],
    summary="审批草稿（draft → approved，字段锁定）",
)
async def approve_message(msg_id: int, db: SessionDep, user: CurrentUser):
    m = await crud.get_message(db, msg_id)
    if m is None:
        raise NotFoundError("外联消息不存在")
    await _check_lead_visible(db, m.lead_id, user)
    m = await crud.transition_message(
        db, m, "approved", approved_by=user.id, locked_at=datetime.now(timezone.utc)
    )
    await db.commit()
    await db.refresh(m)
    return ResponseModel(data=OutreachMessageOut.model_validate(m))


@router.post(
    "/messages/{msg_id}/mark-sent",
    response_model=ResponseModel[OutreachMessageOut],
    summary="标记已发送（approved → sent）——通道回执回调或人工确认",
)
async def mark_sent(
    msg_id: int,
    db: SessionDep,
    user: CurrentUser,
    sent_at: datetime | None = None,
    sent_error: str | None = None,
):
    m = await crud.get_message(db, msg_id)
    if m is None:
        raise NotFoundError("外联消息不存在")
    await _check_lead_visible(db, m.lead_id, user)
    target = "sent" if not sent_error else "bounced"
    m = await crud.transition_message(
        db,
        m,
        target,
        sent_at=sent_at or datetime.now(timezone.utc),
        sent_error=sent_error,
    )
    await db.commit()
    await db.refresh(m)
    return ResponseModel(data=OutreachMessageOut.model_validate(m))


@router.post(
    "/messages/{msg_id}/mark-no-reply",
    response_model=ResponseModel[OutreachMessageOut],
    summary="标记无回复（sent → no_reply）—— 跟进超时 cron 或人工触发",
)
async def mark_no_reply(msg_id: int, db: SessionDep, user: CurrentUser):
    m = await crud.get_message(db, msg_id)
    if m is None:
        raise NotFoundError("外联消息不存在")
    await _check_lead_visible(db, m.lead_id, user)
    m = await crud.transition_message(db, m, "no_reply")
    await db.commit()
    await db.refresh(m)
    return ResponseModel(data=OutreachMessageOut.model_validate(m))


# ---------- 回复收件箱 ----------


@router.get(
    "/replies",
    response_model=ResponseModel[PageResponse[dict]],
    summary="回复列表（带 lead_name）",
)
async def list_replies(
    db: SessionDep,
    user: CurrentUser,
    lead_id: int | None = None,
    intent: str | None = None,
    sentiment: str | None = None,
    unprocessed_only: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    scope_owner_ids, _ = await scope_filter_params(db, user)
    items, total = await crud.list_replies(
        db,
        lead_id=lead_id,
        intent=intent,
        sentiment=sentiment,
        unprocessed_only=unprocessed_only,
        scope_owner_ids=scope_owner_ids,
        page=page,
        page_size=page_size,
    )
    lead_ids = {r.lead_id for r in items}
    lead_map: dict[int, str] = {}
    if lead_ids:
        from app.models.lead import Lead

        rows = (
            await db.execute(select(Lead.id, Lead.name).where(Lead.id.in_(lead_ids)))
        ).all()
        lead_map = {r[0]: r[1] for r in rows}
    out = []
    for r in items:
        d = ReplyOut.model_validate(r).model_dump()
        d["lead_name"] = lead_map.get(r.lead_id, "")
        out.append(d)
    return ResponseModel(
        data=PageResponse[dict](items=out, total=total, page=page, page_size=page_size)
    )


@router.post(
    "/replies",
    response_model=ResponseModel[ReplyOut],
    summary="录入回复（人工或 webhook）；自动 LLM 标注 + CRM 同步",
)
async def create_reply(payload: ReplyCreate, db: SessionDep, user: CurrentUser):
    await _check_lead_visible(db, payload.lead_id, user)
    reply = await agent_ops.record_reply(
        db,
        lead_id=payload.lead_id,
        body=payload.body,
        channel=payload.channel,
        message_id=payload.message_id,
        from_address=payload.from_address,
        subject=payload.subject,
        sentiment=payload.sentiment,
        intent=payload.intent,
        handled_by=user.id,
        auto_sync_crm=True,
    )
    await db.commit()
    await db.refresh(reply)
    return ResponseModel(data=ReplyOut.model_validate(reply))


@router.patch(
    "/replies/{reply_id}/label",
    response_model=ResponseModel[ReplyOut],
    summary="人工覆盖 sentiment/intent（保留审计）",
)
async def override_reply_label(
    reply_id: int, payload: ReplyLabelOverride, db: SessionDep, user: CurrentUser
):
    reply = await crud.get_reply(db, reply_id)
    if reply is None:
        raise NotFoundError("回复不存在")
    await _check_lead_visible(db, reply.lead_id, user)
    reply = await crud.override_reply_label(
        db, reply, sentiment=payload.sentiment, intent=payload.intent
    )
    # 重新触发 CRM 同步
    from app.models.lead import Lead

    lead = await db.get(Lead, reply.lead_id)
    if lead is not None:
        await agent_ops.sync_crm_from_reply(db, reply, lead, handled_by=user.id)
    await db.commit()
    await db.refresh(reply)
    return ResponseModel(data=ReplyOut.model_validate(reply))


@router.post(
    "/replies/sync-crm",
    response_model=ResponseModel[dict],
    summary="手动补救：按 reply.intent 再跑一次 CRM 同步",
)
async def sync_crm(payload: SyncCRMRequest, db: SessionDep, user: CurrentUser):
    from app.models.lead import Lead

    reply = await crud.get_reply(db, payload.reply_id)
    if reply is None:
        raise NotFoundError("回复不存在")
    lead = await db.get(Lead, reply.lead_id)
    if lead is None:
        raise NotFoundError("线索不存在")
    await _check_lead_visible(db, lead.id, user)
    note = await agent_ops.sync_crm_from_reply(
        db, reply, lead, target_status=payload.target_status, handled_by=user.id
    )
    await db.commit()
    return ResponseModel(data={"crm_action": note})


# ---------- 商机预测 ----------


@router.get(
    "/deals",
    response_model=ResponseModel[PageResponse[dict]],
    summary="商机列表（带 lead_name + weighted_amount）",
)
async def list_deals(
    db: SessionDep,
    user: CurrentUser,
    stage: str | None = None,
    is_open: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    scope_owner_ids, _ = await scope_filter_params(db, user)
    items, total = await crud.list_deals(
        db, stage=stage, is_open=is_open, scope_owner_ids=scope_owner_ids, page=page, page_size=page_size
    )
    lead_ids = {d.lead_id for d in items}
    lead_map: dict[int, str] = {}
    if lead_ids:
        from app.models.lead import Lead

        rows = (
            await db.execute(select(Lead.id, Lead.name).where(Lead.id.in_(lead_ids)))
        ).all()
        lead_map = {r[0]: r[1] for r in rows}
    out = []
    for d in items:
        item = ForecastDealOut.model_validate(d).model_dump()
        item["weighted_amount"] = round(d.amount * d.probability / 100.0, 2)
        item["lead_name"] = lead_map.get(d.lead_id, "")
        out.append(item)
    return ResponseModel(
        data=PageResponse[dict](items=out, total=total, page=page, page_size=page_size)
    )


@router.post(
    "/deals",
    response_model=ResponseModel[ForecastDealOut],
    summary="创建商机预测",
)
async def create_deal(payload: ForecastDealCreate, db: SessionDep, user: CurrentUser):
    await _check_lead_visible(db, payload.lead_id, user)
    deal = await crud.create_deal(
        db,
        lead_id=payload.lead_id,
        name=payload.name,
        stage=payload.stage,
        amount=payload.amount,
        probability=payload.probability,
        close_date=payload.close_date,
        is_primary=payload.is_primary,
        note=payload.note,
        owner_id=payload.owner_id or user.id,
    )
    await db.commit()
    await db.refresh(deal)
    out = ForecastDealOut.model_validate(deal).model_dump()
    out["weighted_amount"] = round(deal.amount * deal.probability / 100.0, 2)
    return ResponseModel(data=ForecastDealOut(**out))


@router.patch(
    "/deals/{deal_id}",
    response_model=ResponseModel[ForecastDealOut],
    summary="更新商机",
)
async def update_deal(
    deal_id: int, payload: ForecastDealUpdate, db: SessionDep, user: CurrentUser
):
    deal = await crud.get_deal(db, deal_id)
    if deal is None:
        raise NotFoundError("商机不存在")
    await _check_lead_visible(db, deal.lead_id, user)
    deal = await crud.update_deal(db, deal, payload.model_dump(exclude_unset=True))
    await db.commit()
    await db.refresh(deal)
    out = ForecastDealOut.model_validate(deal).model_dump()
    out["weighted_amount"] = round(deal.amount * deal.probability / 100.0, 2)
    return ResponseModel(data=ForecastDealOut(**out))


@router.delete(
    "/deals/{deal_id}",
    response_model=ResponseModel[dict],
    summary="删除商机",
)
async def delete_deal(deal_id: int, db: SessionDep, user: CurrentUser):
    deal = await crud.get_deal(db, deal_id)
    if deal is None:
        raise NotFoundError("商机不存在")
    await _check_lead_visible(db, deal.lead_id, user)
    await crud.delete_deal(db, deal)
    await db.commit()
    return ResponseModel(data={"deleted": deal_id})


# ---------- 预测看板 ----------


@router.get(
    "/forecast/summary",
    response_model=ResponseModel[ForecastSummaryOut],
    summary="实时预测汇总（按当前 deal 表算）",
)
async def forecast_summary(db: SessionDep, user: CurrentUser):
    scope_owner_ids, _ = await scope_filter_params(db, user)
    data = await crud.compute_forecast_summary(db, scope_owner_ids=scope_owner_ids)
    return ResponseModel(data=ForecastSummaryOut(**data))


@router.post(
    "/forecast/snapshot",
    response_model=ResponseModel[ForecastSnapshotOut],
    summary="计算并持久化快照（cron 调用：weekly / monthly）",
)
async def take_snapshot(
    db: SessionDep,
    _user: CurrentUser,
    period: str = Query(pattern="^(weekly|monthly)$"),
):
    now = datetime.now(timezone.utc)
    if period == "weekly":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
    else:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
    snap = await crud.compute_snapshot(
        db, period=period, period_start=start, period_end=end, scope_owner_ids=None
    )
    await db.commit()
    await db.refresh(snap)
    return ResponseModel(data=ForecastSnapshotOut.model_validate(snap))


@router.get(
    "/forecast/snapshots",
    response_model=ResponseModel[list[ForecastSnapshotOut]],
    summary="历史快照列表",
)
async def list_snapshots(
    db: SessionDep,
    _user: CurrentUser,
    period: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
):
    items = await crud.list_snapshots(db, period=period, limit=limit)
    return ResponseModel(data=[ForecastSnapshotOut.model_validate(s) for s in items])
