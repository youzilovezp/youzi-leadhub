"""代理式销售运营 CRUD 层。

设计原则（沿用 AGENTS.md）：
    - 数据权限（scope_owner_ids）由调用方（endpoint）注入；crud 不认 user
    - 状态机转换在校验函数集中（_assert_transition），避免分散在多个 endpoint 漏校验
    - 不做 ORM 之外的副作用（同步 CRM/触发预测等在 services/agent_ops）
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError, NotFoundError
from app.models.agent import (
    FORECAST_OPEN_STAGES,
    FORECAST_STAGES,
    OUTREACH_EDITABLE_STATUSES,
    OUTREACH_STATUSES,
    OUTREACH_TERMINAL_STATUSES,
    PROBABILITY_BY_STAGE,
    ForecastDeal,
    ForecastSnapshot,
    OutreachMessage,
    OutreachSequence,
    Reply,
)
from app.models.lead import Lead, LeadContact


# ---------- 状态机工具 ----------


def _assert_transition(current: str, target: str) -> None:
    """状态机守门（销售外联消息）。

    合法路径：
        draft → approved
        approved → sent | bounced（发送成功/失败）/ draft（撤回审批）
        sent → replied | no_reply | won | lost
        replied → won | lost（CRM 推进）/ draft（重新联系，重置）
        no_reply → won | lost / draft（重试）
        bounced → draft（重写重发）/ lost（放弃）
        won/lost → 不可流转
    """
    if current == target:
        return
    if current in OUTREACH_TERMINAL_STATUSES:
        raise BusinessError(
            message=f"消息已处于终态 {current}，不可改为 {target}",
        )
    allowed: dict[str, set[str]] = {
        "draft": {"approved", "bounced", "lost"},
        "approved": {"sent", "bounced", "draft"},
        "sent": {"replied", "no_reply", "won", "lost"},
        "replied": {"won", "lost", "draft"},
        "no_reply": {"won", "lost", "draft"},
        "bounced": {"draft", "lost"},
    }
    if target not in allowed.get(current, set()):
        raise BusinessError(message=f"非法状态流转：{current} → {target}")


# ---------- 序列 CRUD ----------


async def list_sequences(
    db: AsyncSession,
    *,
    active: bool | None = None,
    scenario: str | None = None,
    owner_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[OutreachSequence], int]:
    conds = []
    if active is not None:
        conds.append(OutreachSequence.active.is_(active))
    if scenario:
        conds.append(OutreachSequence.scenario == scenario)
    if owner_id is not None:
        from sqlalchemy import or_

        conds.append(
            or_(OutreachSequence.owner_id.is_(None), OutreachSequence.owner_id == owner_id)
        )
    count_stmt = select(func.count()).select_from(OutreachSequence)
    stmt = select(OutreachSequence).order_by(OutreachSequence.id.desc())
    for c in conds:
        count_stmt = count_stmt.where(c)
        stmt = stmt.where(c)
    total = (await db.execute(count_stmt)).scalar_one()
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list((await db.execute(stmt)).scalars().all())
    return items, total


async def get_sequence(db: AsyncSession, seq_id: int) -> OutreachSequence | None:
    return await db.get(OutreachSequence, seq_id)


async def create_sequence(
    db: AsyncSession,
    *,
    name: str,
    steps: list[dict[str, Any]],
    description: str | None = None,
    scenario: str = "first_touch",
    channel: str = "email",
    owner_id: int | None = None,
) -> OutreachSequence:
    seq = OutreachSequence(
        name=name,
        description=description,
        scenario=scenario,
        channel=channel,
        steps=steps,
        active=True,
        owner_id=owner_id,
    )
    db.add(seq)
    await db.flush()
    return seq


async def update_sequence(
    db: AsyncSession, seq: OutreachSequence, patch: dict[str, Any]
) -> OutreachSequence:
    for k, v in patch.items():
        if v is not None:
            setattr(seq, k, v)
    await db.flush()
    return seq


async def delete_sequence(db: AsyncSession, seq: OutreachSequence) -> None:
    await db.delete(seq)
    await db.flush()


# ---------- 外联消息 CRUD ----------


async def get_message(db: AsyncSession, msg_id: int) -> OutreachMessage | None:
    return await db.get(OutreachMessage, msg_id)


async def list_messages(
    db: AsyncSession,
    *,
    lead_id: int | None = None,
    status: str | None = None,
    channel: str | None = None,
    owner_id: int | None = None,
    sequence_id: int | None = None,
    scope_owner_ids: list[int] | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[OutreachMessage], int]:
    conds = []
    if lead_id is not None:
        conds.append(OutreachMessage.lead_id == lead_id)
    if status:
        conds.append(OutreachMessage.status == status)
    if channel:
        conds.append(OutreachMessage.channel == channel)
    if sequence_id is not None:
        conds.append(OutreachMessage.sequence_id == sequence_id)
    if owner_id is not None:
        conds.append(OutreachMessage.owner_id == owner_id)
    if scope_owner_ids is not None:
        from sqlalchemy import or_

        visible = OutreachMessage.owner_id.in_(scope_owner_ids)
        conds.append(or_(OutreachMessage.owner_id.is_(None), visible))
    # 列表 + 计数都跑同条件——避免分页 total 与 items 不一致
    count_stmt = select(func.count()).select_from(OutreachMessage)
    stmt = select(OutreachMessage).order_by(OutreachMessage.created_at.desc())
    for c in conds:
        count_stmt = count_stmt.where(c)
        stmt = stmt.where(c)
    total = (await db.execute(count_stmt)).scalar_one()
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list((await db.execute(stmt)).scalars().all())
    return items, total


async def create_message(
    db: AsyncSession,
    *,
    lead_id: int,
    channel: str,
    body: str,
    subject: str | None = None,
    contact_id: int | None = None,
    sequence_id: int | None = None,
    step_index: int | None = None,
    owner_id: int | None = None,
    llm_generated: bool = False,
    generated_by: str = "template",
    scheduled_at: datetime | None = None,
) -> OutreachMessage:
    """创建草稿。同一 (lead, sequence, step) 仅一条——并发冲突转业务异常。

    用 SAVEPOINT 包住 insert：违反唯一约束时只回滚本次 insert，调用方的外层事务
    继续可用（与现有 crud/lead.py upsert_lead 的 begin_nested 套路一致）。
    """
    msg = OutreachMessage(
        lead_id=lead_id,
        contact_id=contact_id,
        sequence_id=sequence_id,
        step_index=step_index,
        channel=channel,
        subject=subject,
        body=body,
        status="draft",
        llm_generated=llm_generated,
        generated_by=generated_by,
        scheduled_at=scheduled_at,
        owner_id=owner_id,
    )
    db.add(msg)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        raise BusinessError(
            message=f"该线索在此序列该步已存在草稿（lead={lead_id} seq={sequence_id} step={step_index}）",
        ) from exc
    return msg


async def update_message(
    db: AsyncSession, msg: OutreachMessage, patch: dict[str, Any]
) -> OutreachMessage:
    if msg.status not in OUTREACH_EDITABLE_STATUSES:
        raise BusinessError(message=f"消息已锁定（{msg.status}），不可编辑")
    for k, v in patch.items():
        if v is not None:
            setattr(msg, k, v)
    await db.flush()
    return msg


async def transition_message(
    db: AsyncSession, msg: OutreachMessage, target: str, **fields: Any
) -> OutreachMessage:
    """状态机流转 + 附带字段更新（如 sent_at / sent_error / reply_id）。"""
    _assert_transition(msg.status, target)
    msg.status = target
    for k, v in fields.items():
        if v is not None:
            setattr(msg, k, v)
    if target == "approved" and not msg.locked_at:
        msg.locked_at = datetime.now(timezone.utc)
    await db.flush()
    return msg


# ---------- 回复 CRUD ----------


async def get_reply(db: AsyncSession, reply_id: int) -> Reply | None:
    return await db.get(Reply, reply_id)


async def list_replies(
    db: AsyncSession,
    *,
    lead_id: int | None = None,
    intent: str | None = None,
    sentiment: str | None = None,
    unprocessed_only: bool = False,
    scope_owner_ids: list[int] | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Reply], int]:
    """回复列表（数据权限通过 lead 的 owner 反查）。"""
    from sqlalchemy import or_

    from app.models.lead import Lead as LeadModel

    conds = []
    if lead_id is not None:
        conds.append(Reply.lead_id == lead_id)
    if intent:
        conds.append(Reply.intent == intent)
    if sentiment:
        conds.append(Reply.sentiment == sentiment)
    if unprocessed_only:
        conds.append(Reply.processed_at.is_(None))
    if scope_owner_ids is not None:
        subq = select(LeadModel.id).where(
            or_(LeadModel.owner_id.is_(None), LeadModel.owner_id.in_(scope_owner_ids))
        )
        conds.append(Reply.lead_id.in_(subq))
    count_stmt = select(func.count()).select_from(Reply)
    stmt = select(Reply).order_by(Reply.received_at.desc())
    for c in conds:
        count_stmt = count_stmt.where(c)
        stmt = stmt.where(c)
    total = (await db.execute(count_stmt)).scalar_one()
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list((await db.execute(stmt)).scalars().all())
    return items, total


async def create_reply(
    db: AsyncSession,
    *,
    lead_id: int,
    body: str,
    channel: str = "email",
    message_id: int | None = None,
    from_address: str | None = None,
    subject: str | None = None,
    sentiment: str | None = None,
    intent: str | None = None,
    summary: str | None = None,
    llm_labeled: bool = False,
    handled_by: int | None = None,
    received_at: datetime | None = None,
) -> Reply:
    reply = Reply(
        lead_id=lead_id,
        message_id=message_id,
        channel=channel,
        from_address=from_address,
        subject=subject,
        body=body,
        sentiment=sentiment,
        intent=intent,
        summary=summary,
        llm_labeled=llm_labeled,
        handled_by=handled_by,
        received_at=received_at or datetime.now(timezone.utc),
    )
    db.add(reply)
    await db.flush()
    return reply


async def mark_reply_processed(
    db: AsyncSession, reply: Reply, *, crm_action: str | None = None
) -> Reply:
    reply.processed_at = datetime.now(timezone.utc)
    if crm_action:
        reply.crm_action = crm_action
    await db.flush()
    return reply


async def override_reply_label(
    db: AsyncSession,
    reply: Reply,
    *,
    sentiment: str | None = None,
    intent: str | None = None,
) -> Reply:
    """人工覆盖（保留 llm_labeled=True 但置 overridden=True，审计可见）。"""
    if sentiment is not None:
        reply.sentiment = sentiment
    if intent is not None:
        reply.intent = intent
    if sentiment is not None or intent is not None:
        reply.overridden = True
    # 重新处理：清空 processed_at 让 services 重新跑同步
    reply.processed_at = None
    reply.crm_action = None
    await db.flush()
    return reply


# ---------- 商机预测 CRUD ----------


async def list_deals(
    db: AsyncSession,
    *,
    stage: str | None = None,
    owner_id: int | None = None,
    scope_owner_ids: list[int] | None = None,
    is_open: bool | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[ForecastDeal], int]:
    from sqlalchemy import or_

    conds = []
    if stage:
        conds.append(ForecastDeal.stage == stage)
    if owner_id is not None:
        conds.append(ForecastDeal.owner_id == owner_id)
    if is_open is True:
        conds.append(ForecastDeal.stage.in_(FORECAST_OPEN_STAGES))
    elif is_open is False:
        conds.append(ForecastDeal.stage.in_(("won", "lost")))
    if scope_owner_ids is not None:
        visible = ForecastDeal.owner_id.in_(scope_owner_ids)
        conds.append(or_(ForecastDeal.owner_id.is_(None), visible))
    count_stmt = select(func.count()).select_from(ForecastDeal)
    stmt = select(ForecastDeal).order_by(ForecastDeal.close_date.asc().nulls_last())
    for c in conds:
        count_stmt = count_stmt.where(c)
        stmt = stmt.where(c)
    total = (await db.execute(count_stmt)).scalar_one()
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list((await db.execute(stmt)).scalars().all())
    return items, total


async def get_deal(db: AsyncSession, deal_id: int) -> ForecastDeal | None:
    return await db.get(ForecastDeal, deal_id)


async def create_deal(
    db: AsyncSession,
    *,
    lead_id: int,
    name: str,
    stage: str,
    amount: float,
    probability: int | None = None,
    close_date: datetime | None = None,
    is_primary: bool = False,
    note: str | None = None,
    owner_id: int | None = None,
) -> ForecastDeal:
    if probability is None:
        probability = PROBABILITY_BY_STAGE.get(stage, 0)
    # 主商机唯一：先释放旧的
    if is_primary:
        await _release_primary(db, lead_id)
    deal = ForecastDeal(
        lead_id=lead_id,
        name=name,
        stage=stage,
        amount=amount,
        probability=probability,
        close_date=close_date,
        is_primary=is_primary,
        note=note,
        owner_id=owner_id,
    )
    db.add(deal)
    await db.flush()
    return deal


async def _release_primary(db: AsyncSession, lead_id: int) -> None:
    rows = (
        await db.execute(
            select(ForecastDeal).where(
                ForecastDeal.lead_id == lead_id, ForecastDeal.is_primary.is_(True)
            )
        )
    ).scalars().all()
    for r in rows:
        r.is_primary = False
    if rows:
        await db.flush()


async def update_deal(
    db: AsyncSession, deal: ForecastDeal, patch: dict[str, Any]
) -> ForecastDeal:
    new_is_primary = patch.get("is_primary")
    if new_is_primary is True and not deal.is_primary:
        await _release_primary(db, deal.lead_id)
    for k, v in patch.items():
        if v is not None:
            setattr(deal, k, v)
    # 阶段变了 → 若未显式给 probability 则用默认值（保留销售手动设置）
    if "stage" in patch and "probability" not in patch:
        deal.probability = PROBABILITY_BY_STAGE.get(deal.stage, deal.probability)
    await db.flush()
    return deal


async def delete_deal(db: AsyncSession, deal: ForecastDeal) -> None:
    await db.delete(deal)
    await db.flush()


# ---------- 预测汇总 ----------


async def compute_forecast_summary(
    db: AsyncSession,
    *,
    scope_owner_ids: list[int] | None = None,
) -> dict[str, Any]:
    """实时预测（不依赖快照）：加权金额 / 按阶段 / 按跟进人。

    加权 = Σ amount × probability / 100
    """
    from sqlalchemy import or_

    conds = []
    if scope_owner_ids is not None:
        visible = ForecastDeal.owner_id.in_(scope_owner_ids)
        conds.append(or_(ForecastDeal.owner_id.is_(None), visible))
    stmt = select(ForecastDeal)
    for c in conds:
        stmt = stmt.where(c)
    rows = list((await db.execute(stmt)).scalars().all())

    total_amount = 0.0
    weighted_total = 0.0
    open_weighted = 0.0
    by_stage: dict[str, dict[str, float]] = {}
    by_owner: dict[str, dict[str, float]] = {}

    for d in rows:
        w = d.amount * d.probability / 100.0
        total_amount += d.amount
        weighted_total += w
        if d.stage in FORECAST_OPEN_STAGES:
            open_weighted += w
        bs = by_stage.setdefault(d.stage, {"count": 0, "amount": 0.0, "weighted": 0.0})
        bs["count"] += 1
        bs["amount"] += d.amount
        bs["weighted"] += w
        owner_key = str(d.owner_id) if d.owner_id else "unassigned"
        bo = by_owner.setdefault(
            owner_key, {"count": 0, "amount": 0.0, "weighted": 0.0}
        )
        bo["count"] += 1
        bo["amount"] += d.amount
        bo["weighted"] += w

    return {
        "total_amount": round(total_amount, 2),
        "weighted_total": round(weighted_total, 2),
        "open_weighted": round(open_weighted, 2),
        "deal_count": len(rows),
        "open_deal_count": sum(1 for r in rows if r.stage in FORECAST_OPEN_STAGES),
        "by_stage": {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in by_stage.items()},
        "by_owner": {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in by_owner.items()},
    }


async def compute_snapshot(
    db: AsyncSession,
    *,
    period: str,
    period_start: datetime,
    period_end: datetime,
    scope_owner_ids: list[int] | None = None,
) -> ForecastSnapshot:
    """计算并持久化快照（同 period+period_start 唯一，重复跑 upsert）。"""
    if period not in ("weekly", "monthly"):
        raise ValueError(f"period must be weekly|monthly, got {period}")
    data = await compute_forecast_summary(db, scope_owner_ids=scope_owner_ids)
    data["computed_at"] = datetime.now(timezone.utc).isoformat()
    # upsert by (period, period_start)
    existing = (
        await db.execute(
            select(ForecastSnapshot).where(
                ForecastSnapshot.period == period,
                ForecastSnapshot.period_start == period_start,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.period_end = period_end
        existing.data = data
        await db.flush()
        return existing
    snap = ForecastSnapshot(
        period=period, period_start=period_start, period_end=period_end, data=data
    )
    db.add(snap)
    await db.flush()
    return snap


async def list_snapshots(
    db: AsyncSession,
    *,
    period: str | None = None,
    limit: int = 20,
) -> list[ForecastSnapshot]:
    stmt = select(ForecastSnapshot).order_by(ForecastSnapshot.period_start.desc()).limit(limit)
    if period:
        stmt = stmt.where(ForecastSnapshot.period == period)
    return list((await db.execute(stmt)).scalars().all())


# ---------- 校验辅助：目标 lead / contact 存在且可见 ----------


async def assert_lead_visible(
    db: AsyncSession,
    lead_id: int,
    scope_owner_ids: list[int] | None,
) -> Lead:
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise NotFoundError("线索不存在")
    if scope_owner_ids is not None:
        if lead.owner_id is not None and lead.owner_id not in scope_owner_ids:
            # 越权：与现有 collect 详情口径一致，返 404 不泄露存在性
            raise NotFoundError("线索不存在")
    return lead


async def assert_contact_belongs(
    db: AsyncSession, contact_id: int | None, lead_id: int
) -> LeadContact | None:
    if contact_id is None:
        return None
    c = await db.get(LeadContact, contact_id)
    if c is None or c.lead_id != lead_id:
        raise BusinessError(message=f"联系人 #{contact_id} 不属于线索 #{lead_id}")
    return c
