"""商机 CRUD（PRD §37 轻量 CRM）+ 话术审核队列（§56）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError
from app.crud.lead_events import add_event
from app.models.lead import Lead
from app.models.sales import Opportunity, SalesMessage

# 商机阶段词表（漏斗后段，与 follow_status 的 opportunity/quote/negotiation/won 对齐）+ lost
OPPORTUNITY_STAGES: list[dict[str, str]] = [
    {"value": "opportunity", "label": "有效商机"},
    {"value": "quote", "label": "报价"},
    {"value": "negotiation", "label": "谈判"},
    {"value": "won", "label": "成交"},
    {"value": "lost", "label": "失去"},
]


async def get_opportunity(db: AsyncSession, lead_id: int, opp_id: int) -> Opportunity | None:
    return (
        await db.execute(
            select(Opportunity).where(Opportunity.id == opp_id, Opportunity.lead_id == lead_id)
        )
    ).scalar_one_or_none()


async def list_opportunities(db: AsyncSession, lead_id: int) -> list[Opportunity]:
    rows = await db.execute(
        select(Opportunity).where(Opportunity.lead_id == lead_id).order_by(Opportunity.id.desc())
    )
    return list(rows.scalars().all())


async def create_opportunity(
    db: AsyncSession, lead: Lead, payload: Any, *, owner_id: int | None = None
) -> Opportunity:
    """新建商机（stage 固定从 opportunity 起）；联动线索状态到有效商机。"""
    opp = Opportunity(
        lead_id=lead.id,
        name=payload.name,
        amount=payload.amount or 0,
        stage="opportunity",
        expected_close_at=payload.expected_close_at,
        owner_id=owner_id or lead.owner_id,
        note=payload.note,
    )
    db.add(opp)
    await db.flush()
    add_event(
        db,
        lead,
        "opportunity_created",
        payload={"opportunity_id": opp.id, "amount": opp.amount},
        note=f"新增商机：{opp.name}" + (f"（金额 {opp.amount}）" if opp.amount else ""),
    )
    # 线索状态联动推进到有效商机（不回退更后阶段）
    if lead.follow_status in (None, "unassigned", "pending", "contacted", "replied"):
        lead.follow_status = "opportunity"
    await db.flush()
    return opp


async def update_opportunity(
    db: AsyncSession, lead: Lead, opp: Opportunity, payload: Any
) -> Opportunity:
    """推进阶段/改金额；成交联动线索状态与时间。"""
    data = payload.model_dump(exclude_unset=True)
    for field in ("name", "amount", "expected_close_at", "note"):
        if field in data:
            setattr(opp, field, data[field])
    old_stage = opp.stage
    if data.get("stage") and data["stage"] != old_stage:
        if data["stage"] not in {s["value"] for s in OPPORTUNITY_STAGES}:
            raise BusinessError(40001, message=f"非法商机阶段：{data['stage']}")
        opp.stage = data["stage"]
        if data["stage"] == "won":
            opp.won_at = datetime.now(timezone.utc)
            lead.follow_status = "won"
        elif data["stage"] == "lost":
            lead.follow_status = "invalid"
        elif old_stage == "won" and data["stage"] != "won":
            lead.follow_status = "negotiation"  # 从成交回退，退回谈判档
    if owner_id := data.get("owner_id"):
        opp.owner_id = owner_id
    await db.flush()
    if old_stage != opp.stage:
        add_event(
            db,
            lead,
            "opportunity_stage",
            payload={"opportunity_id": opp.id, "old": old_stage, "new": opp.stage},
            note=f"商机「{opp.name}」阶段 {old_stage} → {opp.stage}",
        )
        await db.flush()
    return opp


async def delete_opportunity(db: AsyncSession, lead: Lead, opp: Opportunity) -> None:
    await db.delete(opp)
    await db.flush()


# ---------- 漏斗聚合 ----------

async def funnel_stats(db: AsyncSession) -> dict[str, Any]:
    """销售漏斗（PRD §38）：各阶段线索数 + 商机金额口径。"""
    from app.models.lead import Lead as LeadModel

    rows = (
        await db.execute(
            select(LeadModel.follow_status, func.count()).group_by(LeadModel.follow_status)
        )
    ).all()
    stage_counts = {r[0] or "unassigned": r[1] for r in rows}
    opp_rows = (
        await db.execute(
            select(Opportunity.stage, func.count(), func.coalesce(func.sum(Opportunity.amount), 0))
            .group_by(Opportunity.stage)
        )
    ).all()
    opp_stats = {r[0]: {"count": r[1], "amount": r[2]} for r in opp_rows}
    won_amount = opp_stats.get("won", {}).get("amount", 0)
    won_count = opp_stats.get("won", {}).get("count", 0)
    total_leads = sum(stage_counts.values())
    return {
        "stages": stage_counts,
        "opportunities": opp_stats,
        "won_amount": won_amount,
        "arpu": round(won_amount / won_count) if won_count else 0,  # 按成交商机数
        "total_leads": total_leads,
    }


# ---------- 话术审核队列（§56） ----------

MESSAGE_STATUSES: list[dict[str, str]] = [
    {"value": "draft", "label": "待审核"},
    {"value": "approved", "label": "已通过"},
    {"value": "sent", "label": "已发送"},
    {"value": "rejected", "label": "已驳回"},
]


async def create_message(
    db: AsyncSession,
    lead: Lead,
    content: str,
    *,
    generated_by: str = "llm",
    created_by: int | None = None,
    channel: str = "whatsapp",
) -> SalesMessage:
    msg = SalesMessage(
        lead_id=lead.id, channel=channel, content=content, generated_by=generated_by, created_by=created_by
    )
    db.add(msg)
    await db.flush()
    return msg


async def get_message(db: AsyncSession, msg_id: int) -> SalesMessage | None:
    return (await db.execute(select(SalesMessage).where(SalesMessage.id == msg_id))).scalar_one_or_none()


async def review_message(
    db: AsyncSession, msg: SalesMessage, action: str, *, reviewer_id: int | None = None
) -> SalesMessage:
    """审核（approve/reject）或标记已发（mark_sent，§56 人工复制发送后回填）。"""
    if action == "approve":
        if msg.status != "draft":
            raise BusinessError(40001, message="只有待审核状态可操作")
        msg.status = "approved"
        msg.reviewed_by = reviewer_id
    elif action == "reject":
        if msg.status != "draft":
            raise BusinessError(40001, message="只有待审核状态可操作")
        msg.status = "rejected"
        msg.reviewed_by = reviewer_id
    elif action == "mark_sent":
        if msg.status != "approved":
            raise BusinessError(40001, message="请先审核通过再标记发送")
        msg.status = "sent"
        msg.sent_at = datetime.now(timezone.utc)
    else:
        raise BusinessError(40001, message=f"未知操作：{action}")
    await db.flush()
    return msg
