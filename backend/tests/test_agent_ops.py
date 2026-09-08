"""代理式销售运营（agent）模块测试。

覆盖：
    - draft_outreach：LLM 降级模板、tier1 联系人自动选、status=draft
    - schedule_sequence：批量排程、按 (lead, sequence, step) 唯一去重
    - record_reply + sync_crm_from_reply：LLM 降级 → status 更新 + 事件写入
    - INTENT_TO_STATUS 映射：interested→opportunity、buy_signal→quote
    - 状态机：draft→approved→sent→replied→won + 终态不可流转
    - 预测汇总：按 stage × owner 加权金额
    - 商机 is_primary 唯一：应用层切换主商机
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


@pytest.fixture(autouse=True)
async def _setup(db_session):
    from app.db.init_db import init_db

    await init_db()


async def _make_lead(db_session, name: str = "Test Co"):
    """helper：建一个测试 lead（CN 手动录入）。"""
    from app.collectors.base import LeadDraft
    from app.crud.lead import upsert_lead

    lead, _ = await upsert_lead(
        db_session,
        LeadDraft(name=name, country="CN", industry="跨境电商", source="manual"),
    )
    assert lead is not None, "upsert_lead should not return None for a new draft"
    return lead


async def test_draft_outreach_template_fallback(db_session):
    """无 LLM 配置时降级模板；自动选 tier1 联系人；status=draft。"""
    from app.crud.contact import create_contact
    from app.schemas.collect import ContactCreate
    from app.services import agent_ops

    lead = await _make_lead(db_session, "Acme Co")
    await create_contact(
        db_session,
        lead,
        ContactCreate(name="张总", job_title="CEO", email="zhang@acme.com", confidence=90),
    )
    msg = await agent_ops.draft_outreach(db_session, lead_id=lead.id, channel="email")
    await db_session.commit()
    assert msg.status == "draft"
    assert msg.channel == "email"
    assert msg.llm_generated is False
    assert msg.generated_by == "template"
    assert msg.body, "body 必须非空（降级模板也应有内容）"
    assert msg.subject, "email 通道必须有 subject"
    assert msg.contact_id is not None, "自动选了 tier1 联系人"


async def test_draft_outreach_channel_validation(db_session):
    """非法 channel → BusinessError。"""
    from app.services import agent_ops

    lead = await _make_lead(db_session, "Channel Test")
    with pytest.raises(Exception):
        await agent_ops.draft_outreach(db_session, lead_id=lead.id, channel="fax")


async def test_record_reply_llm_fallback_updates_lead(db_session):
    """无 LLM 时降级 neutral/other → lead 状态机被更新。"""
    from app.services import agent_ops

    lead = await _make_lead(db_session, "Beta Ltd")
    lead.follow_status = "pending"
    await db_session.flush()
    reply = await agent_ops.record_reply(
        db_session,
        lead_id=lead.id,
        body="请问贵司产品价格？",
        channel="email",
    )
    await db_session.commit()
    # LLM 降级 → intent 走 other/question 路径
    assert reply.intent in ("question", "other")
    # lead 状态已被同步（不会停在 pending）
    assert lead.follow_status != "pending"


async def test_record_reply_intent_to_status_mapping(db_session):
    """显式传 intent → 严格按 INTENT_TO_STATUS 映射。"""
    from app.services import agent_ops

    cases = [
        ("interested", "opportunity"),
        ("buy_signal", "quote"),
        ("unsubscribe", "paused"),
        ("question", "contacted"),
    ]
    for intent, expected_status in cases:
        lead = await _make_lead(db_session, f"Test {intent}")
        lead.follow_status = "pending"
        await db_session.flush()
        await agent_ops.record_reply(
            db_session,
            lead_id=lead.id,
            body="body",
            sentiment="positive",
            intent=intent,
            auto_sync_crm=True,
        )
        await db_session.commit()
        assert lead.follow_status == expected_status, (
            f"intent={intent} expected={expected_status} got={lead.follow_status}"
        )


async def test_state_machine_full_path(db_session):
    """状态机：draft → approved → sent → replied → won 全路径合法。"""
    from app.crud import agent as crud

    lead = await _make_lead(db_session, "Gamma")
    msg = await crud.create_message(
        db_session, lead_id=lead.id, channel="email", body="hi"
    )
    await db_session.commit()
    assert msg.status == "draft"

    msg = await crud.transition_message(db_session, msg, "approved", approved_by=lead.owner_id)
    assert msg.status == "approved"
    assert msg.locked_at is not None, "approved 后必须设 locked_at（防编辑锁）"

    msg = await crud.transition_message(
        db_session, msg, "sent", sent_at=datetime.now(timezone.utc)
    )
    assert msg.status == "sent"
    assert msg.sent_at is not None

    msg = await crud.transition_message(db_session, msg, "replied", reply_id=1)
    assert msg.status == "replied"

    msg = await crud.transition_message(db_session, msg, "won")
    assert msg.status == "won"


async def test_state_machine_terminal_blocks(db_session):
    """终态（won）不可再流转。"""
    from app.core.exceptions import BusinessError
    from app.crud import agent as crud

    lead = await _make_lead(db_session, "Delta")
    msg = await crud.create_message(
        db_session, lead_id=lead.id, channel="email", body="x"
    )
    await crud.transition_message(db_session, msg, "approved")
    await crud.transition_message(db_session, msg, "sent")
    await crud.transition_message(db_session, msg, "won")
    with pytest.raises(BusinessError):
        await crud.transition_message(db_session, msg, "lost")


async def test_schedule_sequence_idempotent(db_session):
    """同 (lead, seq, step) 唯一约束：二次排程跳过而非报错。"""
    from app.crud import agent as crud
    from app.services import agent_ops

    seq = await crud.create_sequence(
        db_session,
        name="first_touch",
        steps=[{"day_offset": 0, "channel": "email", "template_hint": "first"}],
    )
    lead = await _make_lead(db_session, "Epsilon")
    r1 = await agent_ops.schedule_sequence(
        db_session, sequence_id=seq.id, lead_ids=[lead.id]
    )
    r2 = await agent_ops.schedule_sequence(
        db_session, sequence_id=seq.id, lead_ids=[lead.id]
    )
    assert r1["scheduled"] == 1
    assert r2["scheduled"] == 0
    assert r2["skipped"] >= 1, "重复排程应被 (lead, seq, step) 唯一约束跳过"


async def test_compute_forecast_summary(db_session):
    """加权金额 = Σ amount × probability / 100。"""
    from app.crud import agent as crud

    lead = await _make_lead(db_session, "Forecast Inc")
    # 商 A: quote ¥10000 prob=70 → 7000
    await crud.create_deal(
        db_session, lead_id=lead.id, name="商 A", stage="quote", amount=10000
    )
    # 商 B: negotiation ¥5000 prob=60 → 3000
    await crud.create_deal(
        db_session, lead_id=lead.id, name="商 B", stage="negotiation", amount=5000
    )
    await db_session.commit()
    summary = await crud.compute_forecast_summary(db_session)
    assert summary["total_amount"] == 15000
    assert summary["weighted_total"] == 10000  # 7000 + 3000
    assert summary["open_weighted"] == 10000  # 都是 open stage
    assert summary["deal_count"] == 2
    assert summary["by_stage"]["quote"]["weighted"] == 7000
    assert summary["by_stage"]["negotiation"]["weighted"] == 3000


async def test_forecast_deal_is_primary_unique_per_lead(db_session):
    """同 lead 主商机唯一：第二个设主 → 第一个自动释放。"""
    from app.crud import agent as crud

    lead = await _make_lead(db_session, "Prim Inc")
    a = await crud.create_deal(
        db_session, lead_id=lead.id, name="A", stage="quote", amount=100, is_primary=True
    )
    b = await crud.create_deal(
        db_session, lead_id=lead.id, name="B", stage="quote", amount=200, is_primary=True
    )
    await db_session.commit()
    await db_session.refresh(a)
    await db_session.refresh(b)
    assert a.is_primary is False, "新设的主商机应释放旧的"
    assert b.is_primary is True


async def test_sync_forecast_from_lead_creates_deal_when_missing(db_session):
    """lead 有 follow_status 但无主商机 → 自动建占位商机。"""
    from app.services import agent_ops

    lead = await _make_lead(db_session, "Auto Deal")
    lead.follow_status = "opportunity"
    await db_session.flush()
    deal = await agent_ops.sync_forecast_from_lead_status(db_session, lead)
    await db_session.commit()
    assert deal is not None
    assert deal.stage == "opportunity"
    assert deal.is_primary is True
    assert deal.probability == 40  # PROBABILITY_BY_STAGE['opportunity']
