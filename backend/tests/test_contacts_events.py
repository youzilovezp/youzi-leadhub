"""联系人 CRUD + 动态事件发射（快照 diff / 防重 / 分层）。"""

import pytest

from app.collectors.base import LeadDraft
from app.crud.contact import (
    _name_from_email,
    auto_create_from_email,
    create_contact,
    derive_seniority,
    update_contact,
)
from app.crud.lead import upsert_lead
from app.models.lead import LeadEvent
from app.schemas.collect import ContactCreate, ContactUpdate

# ---------- seniority 分层 ----------


def test_derive_seniority():
    assert derive_seniority("CEO & Founder") == "tier1"
    assert derive_seniority("总经理") == "tier1"
    assert derive_seniority("Marketing Director") == "tier2"
    assert derive_seniority("客服主管") == "tier2"
    assert derive_seniority("CTO") == "tier3"
    assert derive_seniority("Product Manager") == "tier3"
    assert derive_seniority("Sales Representative") == "unknown"
    assert derive_seniority(None) is None
    assert derive_seniority("  ") is None


def test_name_from_email():
    """邮箱 local-part 推断人名（2026-09-01 用户反馈「联系人几乎都爬不到」：
    英文站联系页常只写邮箱——firstname.lastname / f-l / camelCase 可推断；
    角色邮箱与拆不出的单段串不硬猜）。"""
    assert _name_from_email("john.smith@acme.com") == "John Smith"
    assert _name_from_email("liu-wei@factory.cn") == "Liu Wei"
    assert _name_from_email("jackieMa@t-shine.net") == "Jackie Ma"
    assert _name_from_email("info@acme.com") is None
    assert _name_from_email("sales@acme.com") is None
    assert _name_from_email("marketing@acme.com") is None
    assert _name_from_email("jackiema@t-shine.net") is None  # 全小写拆不出，不硬猜
    assert _name_from_email("yzyd@youduobio.com") is None


# ---------- 事件 + 联系人（DB 集成） ----------


async def _make_lead(db_session, name: str, **kw):
    from app.db.init_db import init_db

    await init_db()
    # 名称/城市/电话与其它测试错开：conftest 的 sqlite 临时库跨测试共享，
    # 身份列（phone/namecity）撞上会被反查合并，事件计数就串了
    draft = LeadDraft(source="google_maps", name=name, country="MY", city="KL", **kw)
    lead, _ = await upsert_lead(db_session, draft)
    await db_session.commit()
    return lead


async def _events(db_session, lead_id: int) -> list[LeadEvent]:
    from sqlalchemy import select

    rows = await db_session.execute(
        select(LeadEvent).where(LeadEvent.lead_id == lead_id).order_by(LeadEvent.id)
    )
    return list(rows.scalars().all())


async def test_create_emits_created_and_contact_events(db_session):
    lead = await _make_lead(db_session, name="Evcreate Sdn Bhd")
    events = await _events(db_session, lead.id)
    types = [e.event_type for e in events]
    assert types == ["source_added"]  # 新建非 manual → source_added
    assert events[0].payload == {"source": "google_maps"}


async def test_merge_flip_emits_whatsapp_events(db_session):
    """合并使 whatsapp_hit False→True：发 whatsapp_found + score_change + grade_change。

    v3 口径：官网 WA +25、在招 WA 岗 +30 = 55 → C 翻 B，grade_change 也发
    （六维时代该画像 31 分停留 C 段不发）。
    """
    lead = await _make_lead(db_session, name="Evmerge Sdn Bhd")
    count_before = len(await _events(db_session, lead.id))

    d2 = LeadDraft(
        source="job_posting",
        name="EVMERGE SDN. BHD.",
        country="MY",
        city="KL",
        whatsapp_url="https://wa.me/6019998888",
        whatsapp_job=True,
        job_signals={"wa_ops": {"label": "WhatsApp 运营/客服", "points": 30}},
        email="hi@eventful.com",
        social={"facebook": "https://facebook.com/eventful"},
    )
    merged, created = await upsert_lead(db_session, d2)
    await db_session.commit()
    assert not created

    events = (await _events(db_session, lead.id))[count_before:]
    types = [e.event_type for e in events]
    assert "whatsapp_found" in types
    assert "whatsapp_job_found" in types
    assert "email_found" in types
    assert "social_found" in types
    assert "source_added" in types  # job_posting 新来源
    assert "score_change" in types
    assert "grade_change" in types  # 0 → 55 分，C → B
    # payload 结构
    wa = next(e for e in events if e.event_type == "whatsapp_found")
    assert wa.payload["url"] == "https://wa.me/6019998888"
    sc = next(e for e in events if e.event_type == "score_change")
    assert sc.payload["old"] < sc.payload["new"]


async def test_same_source_retouch_no_duplicate_events(db_session):
    """同 source 再进：只刷 last_seen，不重复发 source_added/score_change。"""
    lead = await _make_lead(db_session, name="Evretouch Sdn Bhd", phone_raw="0111111111")
    await upsert_lead(
        db_session,
        LeadDraft(source="google_maps", name="Evretouch", country="MY", city="KL", phone_raw="0111111111"),
    )
    await db_session.commit()
    count1 = len(await _events(db_session, lead.id))
    types1 = [e.event_type for e in await _events(db_session, lead.id)]

    await upsert_lead(
        db_session,
        LeadDraft(source="google_maps", name="Evretouch", country="MY", city="KL", phone_raw="0111111111"),
    )
    await db_session.commit()
    events2 = await _events(db_session, lead.id)
    assert len(events2) == count1  # 无新事件
    assert types1.count("source_added") == 1


async def test_contact_crud_and_rescore(db_session):
    lead = await _make_lead(db_session, name="Evcontact Sdn Bhd")
    baseline = lead.score

    contact = await create_contact(
        db_session,
        lead,
        ContactCreate(name="张三", job_title="CEO", email="zhang@acme.com"),
        created_by=None,
    )
    await db_session.commit()
    assert contact.seniority == "tier1"
    assert contact.source == "manual"
    # v3 口径：联系人不进意向分（回答「找谁」，进三问），tier1 也不抬分
    await db_session.refresh(lead)
    assert lead.score == baseline
    types = [e.event_type for e in await _events(db_session, lead.id)]
    assert "contact_added" in types
    # 分数没动就不发 score/grade 变化事件
    assert "score_change" not in types
    assert "grade_change" not in types

    # 同邮箱重复 → 40001
    with pytest.raises(Exception) as exc_info:
        await create_contact(
            db_session, lead, ContactCreate(name="李四", email="zhang@acme.com")
        )
    assert "40001" in str(exc_info.value.code) or "已存在" in str(exc_info.value)

    # 编辑：改职位重新分层
    await update_contact(db_session, lead, contact, ContactUpdate(job_title="Marketing Director"))
    await db_session.commit()
    assert contact.seniority == "tier2"


async def test_auto_create_from_email(db_session):
    lead = await _make_lead(db_session, name="Evauto Sdn Bhd")
    c1 = await auto_create_from_email(db_session, lead, "hi@acme.com")
    await db_session.commit()
    assert c1 is not None
    assert c1.source == "website_enrich"
    assert c1.job_title is None and c1.name is None
    assert c1.confidence == 40
    types = [e.event_type for e in await _events(db_session, lead.id)]
    assert "contact_added" in types

    # 同邮箱再生成 → 跳过（返回 None，不重复）
    assert await auto_create_from_email(db_session, lead, "hi@acme.com") is None
    assert await auto_create_from_email(db_session, lead, "") is None
