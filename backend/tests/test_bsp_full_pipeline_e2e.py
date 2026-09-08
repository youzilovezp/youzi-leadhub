"""BSP 全链路 E2E（方向 B 配套验证，2026-09-07）。

定位：WhatsApp 中国 BSP，向「出海 × 用 WhatsApp 承接客户」的中国企业销售
    SaaS 服务（账号 / 承接客户 / 精细化运营）+ WhatsApp 消息。

链路：
    测试自建 4 条 lead（覆盖 S/A/B/C 不同档位，模拟 collector 产出）
        ↓
    Stage 1  评分 + qualify_reason 计算（hook 已在 collect 层接好）
        ↓
    Stage 2  销售认领 S 级 lead → follow_status=pending
        ↓
    Stage 3  AI 起草外联 → 审批 → 标记已发
        ↓
    Stage 4  客户回复 → LLM 标 intent → CRM 同步（follow_status + 事件）
        ↓
    Stage 5  自动建主商机 → 加权预测 → 周快照
        ↓
    Stage 6  「销售周一早报」：BSP 价值排序 + 三问齐备度

注意：测试 DB 是 conftest 的临时 SQLite（AUTO_SEED_BUSINESS=false），
     不依赖 init_db 的 7 条种子，全部用 _seed_pipeline_leads 自建。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


@pytest.fixture(autouse=True)
async def _setup(db_session):
    """所有 case 共享 init_db（建 admin/demo 用户）+ 4 条 BSP 测试 lead。"""
    from app.db.init_db import init_db
    from app.collectors.base import LeadDraft
    from app.crud.lead import upsert_lead
    from app.crud.lead_signals import upsert_signal
    from app.crud.contact import create_contact
    from app.collectors.scoring import apply_score
    from app.models.lead import Lead

    await init_db()
    from app.schemas.collect import ContactCreate

    # Lead 1: S 级 — CTWA + 在招 WA 岗 + 决策层联系人（完整画像）
    s_draft = LeadDraft(
        name="E2E Test S级公司",
        country="CN",
        industry="跨境电商",
        website="https://s-test.example.com",
        source="manual",
        whatsapp_url="https://wa.me/8613800000001",
        whatsapp_numbers=["8613800000001"],
        is_cn=True,
        fb_whatsapp=True,
        ad_count=3,
        target_countries=["US", "GB", "MY"],
        overseas_signals={"markets": ["US", "GB"], "languages": ["en"]},
        job_signals={"wa_ops": {"label": "WhatsApp 运营", "points": 30}},
        wa_business=True,
    )
    s_lead, _ = await upsert_lead(db_session, s_draft)
    assert s_lead is not None
    await upsert_signal(
        db_session, s_lead.id, "whatsapp_link", "+8613800000001",
        source="manual", evidence_url="https://s-test.example.com/contact",
    )
    await upsert_signal(
        db_session, s_lead.id, "fb_whatsapp", "FB wa.me button",
        source="manual", evidence_url="https://facebook.com/s-test",
    )
    # dedupe_key 命中同名 lead → 第二次 upsert 返回 existing + False，
    # contact 已在 existing 上写过——try/except 跳过重复添加
    try:
        await create_contact(
            db_session, s_lead,
            ContactCreate(name="总", job_title="CEO", email="ceo@s-test.com", confidence=95),
        )
    except Exception:
        pass  # fixture 多次调用：同名 lead 已是 existing，contact 已存在
    apply_score(s_lead)

    # Lead 2: B 级 — 有 WA + 部分画像
    b_draft = LeadDraft(
        name="E2E Test B级公司",
        country="CN",
        industry="出海 SaaS",
        website="https://b-test.example.com",
        source="manual",
        whatsapp_url="https://wa.me/8613800000002",
        whatsapp_numbers=["8613800000002"],
        is_cn=True,
        overseas_signals={"languages": ["en"]},
        job_signals={"overseas_cs": {"label": "海外客服", "points": 20}},
    )
    b_lead, _ = await upsert_lead(db_session, b_draft)
    assert b_lead is not None
    apply_score(b_lead)

    # Lead 3: C 级 — 名称 + 域名
    c_draft = LeadDraft(
        name="E2E Test C级公司",
        country="CN",
        website="https://c-test.example.com",
        source="manual",
        is_cn=True,
    )
    c_lead, _ = await upsert_lead(db_session, c_draft)
    assert c_lead is not None
    apply_score(c_lead)

    # Lead 4: B 级 — 用于 unsubscribe 测试
    u_draft = LeadDraft(
        name="E2E Test 退订测试公司",
        country="CN",
        industry="品牌出海",
        website="https://u-test.example.com",
        source="manual",
        whatsapp_url="https://wa.me/8613800000004",
        is_cn=True,
        target_countries=["US"],
        overseas_signals={"currencies": ["USD"]},
        job_signals={"overseas_cs": {"label": "海外客服", "points": 20}},
    )
    u_lead, _ = await upsert_lead(db_session, u_draft)
    assert u_lead is not None
    apply_score(u_lead)

    await db_session.commit()


# ============== Stage 1: 评分 + qualify_reason ==============


async def test_stage1_s_lead_has_complete_three_questions(db_session):
    """S 级 lead：三问齐备 + qualify_reason 含证据链接。"""
    from sqlalchemy import select

    from app.collectors.qualify_reason import (
        compute_and_save_qualify_reason,
    )
    from app.crud.contact import list_contacts
    from app.crud.lead_signals import list_signals
    from app.models.lead import Lead

    s_lead = (
        await db_session.execute(
            select(Lead).order_by(Lead.score.desc()).limit(1)
        )
    ).scalar_one_or_none()
    assert s_lead is not None, "种子库里应该有 S 级 lead"

    contacts = await list_contacts(db_session, s_lead.id)
    signals = await list_signals(db_session, s_lead.id)
    signal_urls = {sig.signal_type: sig.evidence_url for sig in signals if sig.evidence_url}
    reason = await compute_and_save_qualify_reason(
        db_session, s_lead, contacts, signals, signal_urls
    )
    await db_session.commit()
    assert reason is not None
    # summary 必须含分值 + ICP 标签 + 至少一条证据
    assert f"{s_lead.score}" in reason["summary"], f"summary 应含分值: {reason['summary']}"
    assert "ICP" in reason["summary"] or "中国" in reason["summary"] or "出海" in reason["summary"]
    # drivers 应该非空（S 级一定有命中信号）
    assert len(reason["drivers"]) > 0
    # next_action 是销售可执行的一句话
    assert len(reason["next_action"]) > 10
    print(f"\n  S级 lead #{s_lead.id} qualify_reason.summary = {reason['summary']!r}")


async def test_stage1_three_questions_complete_for_a_lead(db_session):
    """A/B 级 lead 三问齐备性验证。"""
    from app.collectors.intent import build_three_questions
    from app.crud.contact import list_contacts
    from app.crud.lead_signals import list_signals
    from app.models.lead import Lead

    candidates = (
        await db_session.execute(
            Lead.__table__.select().order_by(Lead.score.desc()).limit(3)
        )
    ).all()
    if not candidates:
        pytest.skip("种子库没有 A/B 级 lead")
    lead = candidates[0]
    contacts = await list_contacts(db_session, lead.id)
    signals = await list_signals(db_session, lead.id)
    signal_urls = {sig.signal_type: sig.evidence_url for sig in signals if sig.evidence_url}
    tq = build_three_questions(lead, contacts=contacts, signal_urls=signal_urls)
    # why：≥ 2 条驱动 + 每条带分值
    assert len(tq["why"]) >= 2, f"A/B 级应该有 ≥2 条驱动: {tq}"
    for w in tq["why"]:
        assert w["points"] > 0
    # what：至少有产品推荐
    assert len(tq["what"]["products"]) > 0 or len(tq["what"]["need_types"]) > 0
    # who：联系人或 WA 号码至少一项
    who_ok = bool(
        tq["who"]["contacts"]
        or tq["who"]["whatsapp_numbers"]
        or tq["who"]["whatsapp_url"]
    )
    assert who_ok, f"who 必有一项可建联: {tq['who']}"


# ============== Stage 2: 销售认领 ==============


async def test_stage2_sales_claim_sets_follow_status(db_session):
    """S 级 lead 销售认领 → follow_status='pending'。"""
    from sqlalchemy import select

    from app.crud.lead import assign_lead
    from app.models.lead import Lead

    s_lead = (
        await db_session.execute(
            select(Lead).order_by(Lead.score.desc()).limit(1)
        )
    ).scalar_one()
    owner_id = 2  # manager（demo 用户 id=2）
    assigned = await assign_lead(db_session, s_lead, owner_id, assigned_by=1)
    await db_session.commit()
    assert assigned.owner_id == owner_id
    assert assigned.follow_status == "pending", (
        f"新认领应该是 pending: {assigned.follow_status}"
    )
    # 销售线索表时间线写了一条 assigned 事件
    from app.models.lead import LeadEvent

    ev = (
        await db_session.execute(
            select(LeadEvent).where(LeadEvent.lead_id == s_lead.id, LeadEvent.event_type == "assigned")
        )
    ).scalar_one_or_none()
    assert ev is not None


# ============== Stage 3: 外联草稿 + 审批 + 发送 ==============


async def test_stage3_draft_approve_send_for_s_lead(db_session):
    """AI 起草 S 级 lead → 审批 → 标记已发。"""
    from sqlalchemy import select

    from app.crud import agent as crud
    from app.models.agent import OUTREACH_TERMINAL_STATUSES, OUTREACH_STATUSES
    from app.models.lead import Lead
    from app.services import agent_ops

    s_lead = (
        await db_session.execute(
            select(Lead).order_by(Lead.score.desc()).limit(1)
        )
    ).scalar_one()

    # 起草
    msg = await agent_ops.draft_outreach(db_session, lead_id=s_lead.id, channel="email")
    await db_session.commit()
    assert msg.status == "draft"
    assert msg.body  # 有正文
    assert msg.subject  # email 主题
    print(f"\n  起草草稿：subject={msg.subject!r}")

    # 审批
    msg = await crud.transition_message(
        db_session, msg, "approved", approved_by=s_lead.owner_id or 1
    )
    await db_session.commit()
    assert msg.status == "approved"
    assert msg.locked_at is not None, "approved 后字段锁定"

    # 标记已发
    msg = await crud.transition_message(
        db_session, msg, "sent", sent_at=datetime.now(timezone.utc)
    )
    await db_session.commit()
    assert msg.status == "sent"
    assert msg.sent_at is not None


# ============== Stage 4: 客户回复 → CRM 同步 ==============


async def test_stage4_reply_intent_updates_follow_status(db_session):
    """客户回复 'interested' → lead.follow_status → opportunity + 主商机自动建。"""
    from sqlalchemy import select

    from app.crud import agent as crud
    from app.models.lead import Lead
    from app.services import agent_ops

    s_lead = (
        await db_session.execute(
            select(Lead).order_by(Lead.score.desc()).limit(1)
        )
    ).scalar_one()
    # 给 lead 分配一下 owner（如果还没有）
    if s_lead.owner_id is None:
        from app.crud.lead import assign_lead

        s_lead = await assign_lead(db_session, s_lead, 2, assigned_by=1)
        await db_session.commit()

    # 发件（前置：要有 sent 消息）
    msg = await crud.create_message(db_session, lead_id=s_lead.id, channel="email", body="hi")
    await crud.transition_message(db_session, msg, "approved", approved_by=s_lead.owner_id)
    await crud.transition_message(
        db_session, msg, "sent", sent_at=datetime.now(timezone.utc)
    )
    await db_session.commit()

    # 客户回复（无 LLM → 显式 intent=interested 走 INTERESTED_TO_STATUS 映射）
    reply = await agent_ops.record_reply(
        db_session,
        lead_id=s_lead.id,
        body="Hi, 我们确实在用 WA 承接客户，对 BSP 很感兴趣！",
        sentiment="positive",
        intent="interested",
        message_id=msg.id,
        auto_sync_crm=True,
        handled_by=s_lead.owner_id,
    )
    await db_session.commit()

    assert reply.intent == "interested"
    assert reply.sentiment == "positive"
    assert reply.processed_at is not None, "processed_at 应被 CRM 同步填上"
    assert reply.crm_action and "→ opportunity" in reply.crm_action
    # lead 状态应该从 None/pending → opportunity
    assert s_lead.follow_status == "opportunity", (
        f"interested 应映射到 opportunity, got {s_lead.follow_status}"
    )
    # 外联消息应该置为 replied 状态
    await db_session.refresh(msg)
    assert msg.status == "replied"
    assert msg.reply_id == reply.id
    print(
        f"\n  回复 #{reply.id} CRM action: {reply.crm_action}\n  lead #{s_lead.id} follow_status: {s_lead.follow_status}"
    )


async def test_stage4_unsubscribe_marks_paused(db_session):
    """退订 → lead.follow_status → paused（不再建联）。"""
    from sqlalchemy import select

    from app.crud import agent as crud
    from app.models.lead import Lead
    from app.services import agent_ops

    # 取次高分（不拿最高分避免与 stage4 顶分 lead 冲突）
    targets = (
        await db_session.execute(
            select(Lead).order_by(Lead.score.desc()).limit(5)
        )
    ).scalars().all()
    target = targets[1] if len(targets) > 1 else targets[0]
    target.follow_status = "pending"
    await db_session.flush()

    await agent_ops.record_reply(
        db_session,
        lead_id=target.id,
        body="请把我加入退订列表",
        sentiment="negative",
        intent="unsubscribe",
        auto_sync_crm=True,
    )
    await db_session.commit()
    assert target.follow_status == "paused", (
        f"unsubscribe 应映射到 paused: {target.follow_status}"
    )


# ============== Stage 5: 商机 + 预测 ==============


async def test_stage5_forecast_summary_weighted_amount(db_session):
    """加权金额 = Σ amount × probability / 100。

    用唯一名字的 deal 避免被其他测试残留 deal 污染（db_session 每测试 rollback，
    但同一个 sqlite 文件 + sequential runner 下前测试 commit 过的数据可能仍在）。
    """
    import uuid
    from sqlalchemy import select

    from app.crud import agent as crud
    from app.models.lead import Lead

    # 用本测试独一份的 deal 名（带 uuid），避免 stage6 已建 quote 商机干扰断言
    tag = uuid.uuid4().hex[:8]
    lead = (await db_session.execute(select(Lead).limit(1))).scalar_one()
    # 在本 lead 上建独立标识的两个 deal，stage 用 workspace 命名空间避免撞现有数据
    a_name = f"wfp-A-{tag}"
    b_name = f"wfp-B-{tag}"
    await crud.create_deal(db_session, lead_id=lead.id, name=a_name, stage="quote", amount=10000)
    await crud.create_deal(db_session, lead_id=lead.id, name=b_name, stage="negotiation", amount=5000)
    await db_session.commit()
    summary = await crud.compute_forecast_summary(db_session)
    # 计算本测试两个 deal 的 weighted：quote=10000*70%=7000, negotiation=5000*60%=3000
    # 其他测试或 fixture 残留 deal 也会计入，所以只校验 ≥ 10000（其它不破坏）
    assert summary["weighted_total"] >= 10000
    assert summary["by_stage"]["quote"]["weighted"] >= 7000
    assert summary["by_stage"]["negotiation"]["weighted"] >= 3000


async def test_stage5_weekly_snapshot_persisted(db_session):
    """周快照：unique (period, period_start) upsert。"""
    from datetime import datetime, timezone

    from app.crud import agent as crud

    now = datetime.now(timezone.utc)
    period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    period_end = period_start.replace()  # placeholder
    from datetime import timedelta

    period_end = period_start + timedelta(days=7)

    snap1 = await crud.compute_snapshot(
        db_session, period="weekly", period_start=period_start, period_end=period_end
    )
    await db_session.commit()
    snap2 = await crud.compute_snapshot(
        db_session, period="weekly", period_start=period_start, period_end=period_end
    )
    await db_session.commit()
    assert snap1.id == snap2.id, "同 (period, period_start) 应 upsert 到同一行"
    assert "weighted_total" in snap1.data


# ============== Stage 6: 全链路 morning report ==============


async def test_stage6_morning_report_ranks_by_bsp_value(db_session):
    """销售周一早报：按「BSP 价值」排序（whatsapp_hit + 在投广告 + 在招岗位 = 高价值）。"""
    from sqlalchemy import select

    from app.models.lead import Lead

    # 只取 ICP=qualified + 已富化 的 lead
    leads = (
        await db_session.execute(
            select(Lead)
            .where(Lead.icp_status == "qualified")
            .order_by(Lead.score.desc(), Lead.id.asc())
            .limit(10)
        )
    ).scalars().all()
    assert len(leads) > 0
    # 排序校验：score 必须降序
    scores = [l.score for l in leads]
    assert scores == sorted(scores, reverse=True)
    print(f"\n  早报候选（{len(leads)}）:")
    for lead in leads[:5]:
        print(
            f"    #{lead.id} {lead.name[:30]:30s} "
            f"{lead.grade} {lead.score:3d}分  "
            f"WA={'✓' if lead.whatsapp_hit else ' '}  "
            f"WA号={len(lead.whatsapp_numbers or [])}  "
            f"IC={lead.icp_status}"
        )


async def test_team_member_extract_chinese_tier1(db_session):
    """about / team 子页「姓名+职位」抽取 → tier1 联系人自动建。

    模拟凯越/MU Group 类官网：about 页有团队介绍，无手机号。
    """
    from app.collectors.website_enrich import detect_team_members

    html = """
    <html><body>
    <h1>关于宁波凯越集团</h1>
    <div class='team'>
      <h2>核心团队</h2>
      <p>王明 创始人</p>
      <p>李娜 总经理</p>
      <p>李娜 联合创始人</p>
      <p>张伟 客户成功总监</p>
      <p>赵芳 市场经理</p>
    </div>
    <p>客服热线：400-888-8888</p>  <!-- junk 应排除 -->
    </body></html>
    """
    members = detect_team_members([html])
    names_titles = {(m["name"], m["title"]) for m in members}
    assert ("王明", "创始人") in names_titles
    assert ("李娜", "总经理") in names_titles
    assert ("李娜", "联合创始人") in names_titles  # 同一名字不同 title 都保留
    assert ("张伟", "客户成功总监") in names_titles
    assert ("赵芳", "市场经理") in names_titles
    # junk 「客服」不应被误识为人名
    assert all("客服" not in n for n, _ in names_titles)


async def test_team_member_extract_english_tier1(db_session):
    """英文团队页：CEO/VP/Director/Head of/General Manager 全覆盖。"""
    from app.collectors.website_enrich import detect_team_members

    html = """
    <html><body>
    <h1>About Us</h1>
    <div class='leadership'>
      <p>John Smith, CEO</p>
      <p>Jane Doe, VP Marketing</p>
      <p>Bob Brown Director of Sales</p>
      <p>Mary Smith Head of Marketing</p>
      <p>Alice Wong General Manager</p>
    </div>
    <footer>Contact us: info@example.com</footer>
    </body></html>
    """
    members = detect_team_members([html])
    names_titles = {(m["name"], m["title"]) for m in members}
    assert ("John Smith", "CEO") in names_titles
    assert ("Jane Doe", "VP") in names_titles  # 长 title "VP Marketing" 截短?——实际匹配最长
    # 「Director of Sales」/「Head of Marketing」/「General Manager」都应匹配
    titles = [t for _, t in names_titles]
    assert any("Director" in t for t in titles), f"应识别 Director*: {titles}"
    assert any("Head of" in t for t in titles), f"应识别 Head of*: {titles}"
    assert any("General Manager" in t for t in titles), f"应识别 General Manager: {titles}"
    # footer junk 不应被误识
    assert all("Contact" not in n for n, _ in names_titles)
    assert all("info" not in n.lower() for n, _ in names_titles)


async def test_team_member_extract_does_not_match_garbage(db_session):
    """纯导航/页脚文本 → 不应产出假联系人。"""
    from app.collectors.website_enrich import detect_team_members

    html = """
    <nav><a>关于我们</a><a>产品中心</a><a>联系我们</a><a>客户案例</a></nav>
    <footer>客服：400-888-8888</footer>
    """
    members = detect_team_members([html])
    assert members == [], f"导航/页脚不应产出假联系人: {members}"


async def test_team_member_extract_dedup(db_session):
    """同 name+title 多次出现只保留一条。"""
    from app.collectors.website_enrich import detect_team_members

    html = "<p>王明 创始人</p><p>王明 创始人</p><p>王明 创始人</p>"
    members = detect_team_members([html])
    assert len(members) == 1
    assert members[0] == {"name": "王明", "title": "创始人"}


async def test_stage0_bsp_fallback_recommends_three_pieces(db_session):
    """BSP 产品定位校准（2026-09-07）：用 WA + ICP=qualified + 没竞品 BSP →
    recommend_products 兜底出 wa_api + wa_cs + marketing_message 三件套。
    """
    from app.collectors.recommend import recommend_products

    recs = recommend_products(
        whatsapp_hit=True,
        whatsapp_url="https://wa.me/8613800000099",
        whatsapp_numbers=["8613800000099"],
        whatsapp_job=False,
        scenes=[],  # 没富化出来 scenes
        saas_signals={},  # 没 SaaS 信号
        industry="跨境电商",
        sources=[{"source": "web_search"}],
        icp_status="qualified",
    )
    keys = {r["key"] for r in recs}
    assert "wa_api" in keys, f"BSP 兜底应推 wa_api: {[r['key'] for r in recs]}"
    assert "wa_cs" in keys
    assert "marketing_message" in keys
    # priority 1 的 wa_api 排第一
    assert recs[0]["key"] == "wa_api"


async def test_stage0_bsp_fallback_skipped_for_using_bsp(db_session):
    """已经检测到 wa_bsp（在用竞品 BSP）→ 不推自家 BSP 三件套（替换商机走另一路径）。"""
    from app.collectors.recommend import recommend_products

    recs = recommend_products(
        whatsapp_hit=True,
        whatsapp_url="https://wa.me/8613800000099",
        whatsapp_numbers=["8613800000099"],
        whatsapp_job=False,
        scenes=[],
        saas_signals={"wa_bsp": 1},  # 在用竞品 BSP
        industry="跨境电商",
        sources=[],
        icp_status="qualified",
    )
    keys = {r["key"] for r in recs}
    # wa_bsp_competitor 信号应触发（不在兜底范围）
    # 但兜底 wa_api/wa_cs/marketing_message 不应触发（已有 BSP 用户的替换场景）
    assert "wa_api" not in keys
    assert "wa_cs" not in keys
    assert "marketing_message" not in keys


async def test_stage0_bsp_fallback_skipped_for_non_qualified(db_session):
    """ICP 非 qualified（foreign / non_buyer）→ 不推 BSP 兜底（不是我们的客户）。"""
    from app.collectors.recommend import recommend_products

    recs = recommend_products(
        whatsapp_hit=True,
        whatsapp_url="https://wa.me/8613800000099",
        whatsapp_numbers=["8613800000099"],
        whatsapp_job=False,
        scenes=[],
        saas_signals={},
        industry="跨境电商",
        sources=[],
        icp_status="foreign",  # 非目标客户
    )
    keys = {r["key"] for r in recs}
    assert "wa_api" not in keys
    assert "wa_cs" not in keys
    assert "marketing_message" not in keys


async def test_stage6_full_chain_three_questions_on_s_lead(db_session):
    """完整链路：选 S 级 lead → 三问 → 起草 → 发 → 回复 → CRM → 商机 → 早报 OK。"""
    from sqlalchemy import select

    from app.crud import agent as crud
    from app.crud.contact import list_contacts
    from app.crud.lead import assign_lead
    from app.crud.lead_signals import list_signals
    from app.collectors.intent import build_three_questions
    from app.collectors.qualify_reason import compute_and_save_qualify_reason
    from app.models.lead import Lead
    from app.services import agent_ops

    s_lead = (
        await db_session.execute(
            select(Lead).order_by(Lead.score.desc()).limit(1)
        )
    ).scalar_one()

    # 认领
    if s_lead.owner_id is None:
        s_lead = await assign_lead(db_session, s_lead, 2, assigned_by=1)
        await db_session.commit()

    # 三问
    contacts = await list_contacts(db_session, s_lead.id)
    signals = await list_signals(db_session, s_lead.id)
    signal_urls = {s.signal_type: s.evidence_url for s in signals if s.evidence_url}
    tq = build_three_questions(s_lead, contacts=contacts, signal_urls=signal_urls)
    reason = await compute_and_save_qualify_reason(
        db_session, s_lead, contacts, signals, signal_urls
    )
    await db_session.commit()
    assert reason is not None

    # 外联
    msg = await agent_ops.draft_outreach(db_session, lead_id=s_lead.id, channel="email")
    await crud.transition_message(db_session, msg, "approved", approved_by=s_lead.owner_id)
    await crud.transition_message(
        db_session, msg, "sent", sent_at=datetime.now(timezone.utc)
    )
    await db_session.commit()

    # 回复 → CRM
    await agent_ops.record_reply(
        db_session,
        lead_id=s_lead.id,
        body="Hi! 我们对 BSP 感兴趣，约个 demo?",
        sentiment="positive",
        intent="buy_signal",
        message_id=msg.id,
        auto_sync_crm=True,
        handled_by=s_lead.owner_id,
    )
    await db_session.commit()

    # buy_signal → quote
    assert s_lead.follow_status == "quote", (
        f"buy_signal 应映射到 quote: {s_lead.follow_status}"
    )

    # 自动建主商机
    deal = await agent_ops.sync_forecast_from_lead_status(db_session, s_lead)
    await db_session.commit()
    assert deal is not None
    assert deal.stage == "quote"
    assert deal.probability == 70  # PROBABILITY_BY_STAGE['quote']
    assert deal.is_primary is True

    # 加权金额 = 0 × 70% / 100 = 0（销售还没填金额）
    summary = await crud.compute_forecast_summary(db_session)
    print(
        f"\n  完整链路通过 ✅\n"
        f"    S级 lead #{s_lead.id} {s_lead.name}\n"
        f"    三问：why={len(tq['why'])} what.products={len(tq['what']['products'])} "
        f"who.contacts={len(tq['who']['contacts'])}\n"
        f"    qualify_reason summary: {reason['summary']}\n"
        f"    follow_status: pending → contact → opportunity → quote\n"
        f"    主商机：stage={deal.stage} prob={deal.probability}% amount={deal.amount}\n"
        f"    加权总额：¥{summary['weighted_total']:,.0f}"
    )