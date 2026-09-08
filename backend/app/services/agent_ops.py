"""代理式销售运营服务层（核心业务编排）。

负责：
    1. draft_outreach      LLM 起草个性化外联（基于 lead + contact + context）
    2. schedule_sequence   对一组线索按序列批量排程草稿
    3. record_reply        录入回复 → LLM 标注 → CRM 同步（状态机 + lead_events）
    4. sync_crm_from_reply 根据 reply.intent 更新 lead.follow_status + 写事件

所有写操作 commit 由 endpoint 层负责；服务层只 flush，让调用方原子提交。
LLM 失败一律降级到模板（与现有 services/llm.py 一致），不让 LLM 抖动拖垮业务。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.recommend import recommend_products
from app.core.exceptions import BusinessError, NotFoundError
from app.crud import agent as crud_agent
from app.models.agent import (
    FORECAST_STAGES,
    OUTREACH_CHANNELS,
    PROBABILITY_BY_STAGE,
    REPLY_INTENT_LABELS_ZH,
    REPLY_INTENTS,
    REPLY_SENTIMENT_LABELS_ZH,
    REPLY_SENTIMENTS,
    ForecastDeal,
    OutreachMessage,
    OutreachSequence,
    Reply,
)
from app.models.lead import Lead, LeadContact, LeadEvent
from app.services import llm


# ---------- 1. LLM 起草 ----------


_DRAFT_SYSTEM = """你是 WhatsApp Business API / SaaS 销售。基于给出的企业画像与联系人，
写一封首次建联文案（{channel}渠道）。
- 中文输出
- 150-300 字
- 点出对方业务与 WhatsApp 使用现状 → 我们能帮到什么 → 一个轻量行动请求
- 不堆术语，不要套话
输出 JSON：{"subject": "邮件主题（仅 email 必填，<=30字）", "body": "正文"}
{extra}
只输出 JSON。"""


def _lead_context_for_draft(lead: Lead, contact: LeadContact | None, hint: str | None) -> str:
    parts = [
        f"企业：{lead.name}（行业 {lead.industry or '未知'}，国家 {lead.country or '未知'}）",
        f"等级：{lead.grade}（意向分 {lead.score}）",
        f"WhatsApp：{'已发现 ' + (lead.whatsapp_url or '') if lead.whatsapp_hit else '未检测'}",
        f"WhatsApp 号码：{', '.join((lead.whatsapp_numbers or [])[:3]) or '无'}",
        f"FB 私域：{'是' if lead.fb_whatsapp else '否'}；在招 WA 岗位：{'是' if lead.whatsapp_job else '否'}",
        f"场景：{', '.join(lead.scenes or []) or '未检测'}",
        f"出海信号：{lead.overseas_signals or '无'}",
        f"官网：{lead.website or '无'}；邮箱：{lead.email or '无'}",
    ]
    if contact is not None:
        parts.append(
            f"联系人：{contact.name or '未填'}（{contact.job_title or '职位待补'}），"
            f"邮箱 {contact.email or '无'}，电话 {contact.phone or '无'}"
        )
    if hint:
        parts.append(f"销售备注：{hint}")
    return "\n".join(parts)


def _draft_fallback(
    lead: Lead,
    contact: LeadContact | None,
    channel: str,
    hint: str | None,
) -> tuple[str | None, str]:
    """LLM 不可用或失败时的模板草稿。"""
    uses_wa = bool(
        lead.whatsapp_hit or lead.whatsapp_url or (lead.whatsapp_numbers or [])
    )
    recs = recommend_products(
        whatsapp_hit=lead.whatsapp_hit,
        whatsapp_url=lead.whatsapp_url,
        whatsapp_numbers=list(lead.whatsapp_numbers or []),
        whatsapp_job=lead.whatsapp_job,
        scenes=lead.scenes,
        saas_signals=lead.saas_signals,
        industry=lead.industry,
        sources=lead.sources,
        icp_status=lead.icp_status,
    )
    top = recs[0]["name"] if recs else "WhatsApp 商业化解决方案"
    contact_name = contact.name.split()[0] if contact and contact.name else "您好"
    body = (
        f"{contact_name}，注意到贵司（{lead.name}）"
        f"{'已提供 WhatsApp 联系入口' if uses_wa else '正在服务海外市场'}，"
        f"我们专注帮助出海企业统一管理 WhatsApp 客服与营销触达，{top}已在同行业客户落地。"
        "如果贵司在多客服账号管理、客户分配或营销自动化上有困扰，"
        "欢迎约 15 分钟交流，我可以发一份方案给您。"
    )
    if hint:
        body += f"\n\n（销售备注：{hint}）"
    subject = f"{lead.name} × WhatsApp 客服升级" if channel == "email" else None
    return subject, body


async def draft_outreach(
    db: AsyncSession,
    *,
    lead_id: int,
    channel: str,
    contact_id: int | None = None,
    context_hint: str | None = None,
    sequence_id: int | None = None,
    step_index: int | None = None,
    owner_id: int | None = None,
) -> OutreachMessage:
    """起草单条个性化外联（不发送；status=draft）。"""
    if channel not in OUTREACH_CHANNELS:
        raise BusinessError(message=f"非法通道：{channel}")
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise NotFoundError("线索不存在")
    contact: LeadContact | None = None
    if contact_id is not None:
        contact = await db.get(LeadContact, contact_id)
        if contact is None or contact.lead_id != lead_id:
            raise BusinessError(message=f"联系人 #{contact_id} 不属于线索 #{lead_id}")
    # 选联系人：显式 > 决策层（tier1）> 任意 > None
    if contact is None:
        from sqlalchemy import select

        rows = (
            await db.execute(
                select(LeadContact)
                .where(LeadContact.lead_id == lead_id)
                .order_by(
                    # tier1 优先；同 tier 按 confidence desc
                    (LeadContact.seniority == "tier1").desc(),
                    LeadContact.confidence.desc(),
                )
                .limit(1)
            )
        ).scalars().all()
        contact = rows[0] if rows else None

    extra = "这是 WhatsApp 消息，请用简短口语化中文（≤80 字），不开头问候语堆叠。"
    if channel == "linkedin":
        extra = "LinkedIn InMail 风格，开头不要直呼 '您好'，开门见山。"
    if channel == "email":
        extra = "邮件格式：主题在 subject，正文在 body。"

    subject, body = _draft_fallback(lead, contact, channel, context_hint)
    generated_by = "template"

    if llm.llm_enabled():
        try:
            result = await llm.chat_json(
                _DRAFT_SYSTEM.format(channel=channel, extra=extra),
                _lead_context_for_draft(lead, contact, context_hint),
            )
            subject = (result.get("subject") or subject) if channel == "email" else None
            body = result.get("body") or body
            generated_by = "llm"
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "agent.draft_outreach 降级模板：{}: {}", type(exc).__name__, exc
            )

    msg = await crud_agent.create_message(
        db,
        lead_id=lead_id,
        channel=channel,
        body=body,
        subject=subject,
        contact_id=contact.id if contact else None,
        sequence_id=sequence_id,
        step_index=step_index,
        owner_id=owner_id,
        llm_generated=(generated_by == "llm"),
        generated_by=generated_by,
    )
    return msg


# ---------- 2. 序列排程 ----------


async def schedule_sequence(
    db: AsyncSession,
    *,
    sequence_id: int,
    lead_ids: list[int],
    start_at: datetime | None = None,
    owner_id: int | None = None,
) -> dict[str, Any]:
    """对一组线索按序列批量生成草稿并排程。

    跳过条件：
        - lead 不存在
        - 序列 step 在 (lead, sequence, step) 已存在草稿（防重排）
    """
    seq = await db.get(OutreachSequence, sequence_id)
    if seq is None:
        raise NotFoundError("序列不存在")
    if not seq.active:
        raise BusinessError(message="序列已停用")
    steps = list(seq.steps or [])
    if not steps:
        raise BusinessError(message="序列未配置步骤")
    base = start_at or datetime.now(timezone.utc)
    scheduled = 0
    skipped = 0
    errors: list[str] = []
    for lead_id in lead_ids:
        lead = await db.get(Lead, lead_id)
        if lead is None:
            skipped += 1
            errors.append(f"lead #{lead_id} 不存在")
            continue
        for step in steps:
            offset = int(step.get("day_offset", 0))
            channel = str(step.get("channel") or seq.channel)
            step_idx = steps.index(step)
            # 跳过已存在
            from sqlalchemy import select

            existing = (
                await db.execute(
                    select(OutreachMessage.id).where(
                        OutreachMessage.lead_id == lead_id,
                        OutreachMessage.sequence_id == sequence_id,
                        OutreachMessage.step_index == step_idx,
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                skipped += 1
                continue
            scheduled_at = base + timedelta(days=offset)
            try:
                await draft_outreach(
                    db,
                    lead_id=lead_id,
                    channel=channel,
                    sequence_id=sequence_id,
                    step_index=step_idx,
                    owner_id=owner_id,
                )
                # 排程时间写到最新 draft
                latest = (
                    await db.execute(
                        select(OutreachMessage)
                        .where(
                            OutreachMessage.lead_id == lead_id,
                            OutreachMessage.sequence_id == sequence_id,
                            OutreachMessage.step_index == step_idx,
                        )
                    )
                ).scalars().first()
                if latest is not None:
                    latest.scheduled_at = scheduled_at
                scheduled += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"lead #{lead_id} step {step_idx}: {exc}")
                logger.warning(
                    "schedule_sequence lead={} step={} 失败：{}: {}",
                    lead_id,
                    step_idx,
                    type(exc).__name__,
                    exc,
                )
    await db.flush()
    return {"scheduled": scheduled, "skipped": skipped, "errors": errors[:50]}


# ---------- 3. 录入回复 + LLM 标注 ----------


async def label_reply_with_llm(body: str) -> tuple[str, str, str, bool]:
    """LLM 标注 sentiment + intent + summary + llm_labeled 标记。"""
    from app.services import llm as _llm

    if not _llm.llm_enabled():
        raise RuntimeError("LLM 未配置")
    sys_prompt = (
        "你是销售回复分析助手。基于回复正文判断情感（negative/neutral/positive）、"
        "购买意向（" + "/".join(REPLY_INTENTS) + "）、并用一句中文摘要。"
        "输出 JSON：{\"sentiment\": \"...\", \"intent\": \"...\", \"summary\": \"...\"}。只输出 JSON。"
    )
    user_prompt = f"回复正文：\n{body[:3000]}"
    result = await _llm.chat_json(sys_prompt, user_prompt)
    sentiment = result.get("sentiment") or "neutral"
    intent = result.get("intent") or "other"
    if sentiment not in REPLY_SENTIMENTS:
        sentiment = "neutral"
    if intent not in REPLY_INTENTS:
        intent = "other"
    summary = (result.get("summary") or "")[:512]
    return sentiment, intent, summary, True


async def record_reply(
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
    handled_by: int | None = None,
    auto_sync_crm: bool = True,
) -> Reply:
    """录入回复：自动 LLM 标注（缺失时） → 自动同步 CRM → 写事件。

    auto_sync_crm=False 用于「先入库、再异步批量处理」的吞吐场景。
    """
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise NotFoundError("线索不存在")

    llm_labeled = False
    if sentiment is None or intent is None:
        try:
            s, i, sm, was_llm = await label_reply_with_llm(body)
            sentiment = sentiment or s
            intent = intent or i
            summary = summary or sm
            llm_labeled = was_llm
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "reply LLM 标注失败，降级 neutral/other：{}: {}",
                type(exc).__name__,
                exc,
            )
            sentiment = sentiment or "neutral"
            intent = intent or "other"
            summary = summary or ""

    reply = await crud_agent.create_reply(
        db,
        lead_id=lead_id,
        body=body,
        channel=channel,
        message_id=message_id,
        from_address=from_address,
        subject=subject,
        sentiment=sentiment,
        intent=intent,
        summary=summary,
        llm_labeled=llm_labeled,
        handled_by=handled_by,
    )

    # 关联外联：传 message_id → 该消息置 replied 状态 + 绑定 reply_id
    if message_id is not None:
        msg = await db.get(OutreachMessage, message_id)
        if msg is not None and msg.lead_id == lead_id:
            await crud_agent.transition_message(
                db, msg, "replied", reply_id=reply.id, sent_at=msg.sent_at
            )

    if auto_sync_crm:
        await sync_crm_from_reply(db, reply, lead, handled_by=handled_by)
    return reply


# ---------- 4. CRM 同步（核心：intent → lead.follow_status） ----------


# intent → 目标 follow_status（与 schemas/collect.py 的 10 态词表对齐）
# None = 不变更 lead 状态（如自动回执/假期）
INTENT_TO_STATUS: dict[str, str | None] = {
    "interested": "opportunity",
    "buy_signal": "quote",
    "question": "contacted",
    "objection": "contacted",
    "wrong_person": "contacted",
    "out_of_office": None,
    "unsubscribe": "paused",
    "other": "contacted",
}


async def sync_crm_from_reply(
    db: AsyncSession,
    reply: Reply,
    lead: Lead,
    *,
    target_status: str | None = None,
    handled_by: int | None = None,
) -> str:
    """根据 reply.intent（或显式 target_status）更新 lead 状态机 + 写事件。

    幂等：reply 已 processed_at 不为空时跳过（避免重复触发）。
    返回最终应用的 CRM action 描述。
    """
    if reply.processed_at is not None:
        return reply.crm_action or "已处理，跳过"
    target = target_status or INTENT_TO_STATUS.get(reply.intent or "other", "contacted")
    if target is None:
        await crud_agent.mark_reply_processed(
            db, reply, crm_action=f"intent={reply.intent} 不变更状态"
        )
        return reply.crm_action or ""

    old_status = lead.follow_status
    lead.follow_status = target
    lead.last_followed_at = datetime.now(timezone.utc)
    if lead.next_follow_at is None:
        lead.next_follow_at = datetime.now(timezone.utc) + timedelta(days=3)

    note = (
        f"回复 #{reply.id}：sentiment={reply.sentiment} intent={reply.intent} → "
        f"{old_status or '未跟进'} → {target}"
    )
    db.add(
        LeadEvent(
            lead_id=lead.id,
            event_type="reply_received",
            payload={
                "reply_id": reply.id,
                "intent": reply.intent,
                "sentiment": reply.sentiment,
                "old_status": old_status,
                "new_status": target,
            },
            note=note,
            created_by=handled_by,
        )
    )
    await crud_agent.mark_reply_processed(db, reply, crm_action=note)
    await db.flush()
    return note


# ---------- 5. 商机 → 预测自动同步（手动 hook） ----------


async def sync_forecast_from_lead_status(
    db: AsyncSession, lead: Lead, *, owner_id: int | None = None
) -> ForecastDeal | None:
    """根据 lead.follow_status 同步主商机预测行（不存在则建）。

    不覆盖销售已存在的商机——只在没有主商机时按 status 建一份占位（amount=0，
    销售自行填金额）。返回主商机行。
    """
    from sqlalchemy import select

    existing = (
        await db.execute(
            select(ForecastDeal).where(
                ForecastDeal.lead_id == lead.id, ForecastDeal.is_primary.is_(True)
            )
        )
    ).scalars().first()
    if existing is not None:
        # 阶段对齐（不改金额 / 概率——那是销售自管）
        new_prob = PROBABILITY_BY_STAGE.get(lead.follow_status or "pending", existing.probability)
        if existing.stage != lead.follow_status:
            existing.stage = lead.follow_status or "pending"
            existing.probability = new_prob
            await db.flush()
        return existing
    if not lead.follow_status:
        return None
    if lead.follow_status not in FORECAST_STAGES:
        return None
    return await crud_agent.create_deal(
        db,
        lead_id=lead.id,
        name=f"{lead.name} · {REPLY_INTENT_LABELS_ZH.get('interested', '主商机')}",
        stage=lead.follow_status,
        amount=0.0,
        owner_id=owner_id or lead.owner_id,
        is_primary=True,
    )
