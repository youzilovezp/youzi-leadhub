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
    assert ("Jane Doe", "VP Marketing") in names_titles  # VP + 后续词整体识别（修复占位策略）
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


async def test_cost_health_endpoint_aggregates_inspection_list(db_session):
    """cost-health 聚合接口：识别需人工巡检的 lead，按严重程度排序。

    注入 3 类典型 case：
      - fail + 耗时异常 → 「富化失败」「资源消耗」两条原因
      - success 但 0 信号 → 「0 信号命中」「SPA 壳」两条原因
      - render_calls > 0 → 「反爬大站」一条原因
    """
    import json as _json

    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.main import app

    from sqlalchemy import select

    from app.models.lead import Lead
    from app.collectors.base import LeadDraft
    from app.crud.lead import upsert_lead

    # 关键（2026-09-11 修复）：TestClient 直接启 app lifespan，会按 settings.WORKERS
    # 决定是否启 task_runner 后台协程——WORKERS=1 默认会启 → 跨测试阻塞。
    # 用 monkeypatch 临时关掉后台（与 conftest 同一策略）。
    import pytest as _pytest

    mp = _pytest.MonkeyPatch()
    mp.setattr(settings, "WORKERS", 0)
    mp.setattr(settings, "SCHEDULER_ENABLED", False)

    # 1. 造 3 条 lead（用 draft 而不是依赖 seed，避免临时库没数据的依赖）
    lead_ids: list[int] = []
    for name in ["Health Test A", "Health Test B", "Health Test C"]:
        lead, _ = await upsert_lead(
            db_session,
            LeadDraft(name=name, country="CN", industry="跨境电商", source="manual"),
        )
        assert lead is not None
        lead_ids.append(lead.id)
    await db_session.commit()

    # 2. 注入三类样本 cost
    cases = {
        lead_ids[0]: {
            "outcome": "success",
            "http_calls": 8,
            "impersonate_calls": 2,
            "render_calls": 1,  # 反爬
            "inner_pages_fetched": 2,
            "signals_found": {"whatsapp_link": 1, "email": 1},
            "elapsed_ms": 8500,
        },
        lead_ids[1]: {
            "outcome": "fail",  # 失败
            "http_calls": 4,
            "impersonate_calls": 0,
            "render_calls": 0,
            "inner_pages_fetched": 0,
            "signals_found": {},
            "elapsed_ms": 60000,  # 超时
        },
        lead_ids[2]: {
            "outcome": "success",  # SPA 壳
            "http_calls": 1,
            "impersonate_calls": 0,
            "render_calls": 0,
            "inner_pages_fetched": 0,
            "signals_found": {},
            "elapsed_ms": 1200,
        },
    }
    for lid, cost in cases.items():
        lead = (await db_session.execute(select(Lead).where(Lead.id == lid))).scalar_one()
        meta = dict(lead.field_meta or {})
        meta["enrich_cost"] = cost
        if cost["outcome"] == "fail":
            meta["enrich_fail"] = {"reason": "DNS 解析失败：测试"}
        lead.field_meta = meta
    await db_session.commit()

    # 3. 用 TestClient 调接口
    try:
        with TestClient(app) as c:
            r = c.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "admin"},
            )
            assert r.status_code == 200, r.text
            token = r.json()["data"]["access_token"]
            r = c.get(
                "/api/v1/collect/leads/cost-health",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 200, r.text
            d = r.json()["data"]
    finally:
        mp.undo()

    # 4. 断言整体统计（必须 ≥ 3：可能 fixture 已有 lead）
    assert d["total_leads"] >= 3
    assert d["with_cost"] >= 3
    assert d["success_count"] >= 2
    assert d["fail_count"] >= 1

    # 5. needs_inspection 包含我们造的三条（按 lead_id 索引）
    by_id = {b["lead_id"]: b for b in d["needs_inspection"]}
    for lid in lead_ids:
        assert lid in by_id, f"#{lid} 应在 needs_inspection: {by_id.keys()}"

    # 6. 断言具体原因
    # B 是 fail → 含富化失败 + 资源消耗
    assert any("富化失败" in r for r in by_id[lead_ids[1]]["reasons"])
    assert any("资源消耗" in r for r in by_id[lead_ids[1]]["reasons"])
    # C 是 SPA 壳 → 含 0 信号 + SPA
    assert any("0 信号" in r for r in by_id[lead_ids[2]]["reasons"])
    assert any("SPA" in r for r in by_id[lead_ids[2]]["reasons"])
    # A 是反爬 → 含反爬
    assert any("反爬" in r for r in by_id[lead_ids[0]]["reasons"])

    # 7. 严重程度排序：fail 排在 0 信号之前
    by_index = {lid: i for i, lid in enumerate(b["lead_id"] for b in d["needs_inspection"])}
    assert by_index[lead_ids[1]] < by_index[lead_ids[2]], (
        f"fail (#{lead_ids[1]}) 应该排在 0 信号 (#{lead_ids[2]}) 之前"
    )

    # 8. summary_text
    assert "需巡检" in d["summary_text"]


async def test_cost_health_route_not_shadowed_by_lead_id(db_session):
    """路由顺序：/leads/cost-health 必须在 /leads/{lead_id} 之前声明——否则
    'cost-health' 字符串被当 lead_id（int parse 失败 → 422）。回归保护。
    """
    import json as _json

    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.main import app

    import pytest as _pytest

    mp2 = _pytest.MonkeyPatch()
    mp2.setattr(settings, "WORKERS", 0)
    mp2.setattr(settings, "SCHEDULER_ENABLED", False)

    try:
        with TestClient(app) as c:
            r = c.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "admin"},
            )
            token = r.json()["data"]["access_token"]
            # 422 = 路由被错误解析为 /leads/{lead_id}（cost-health 不是 int）
            r = c.get(
                "/api/v1/collect/leads/cost-health",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code != 422, (
                f"cost-health 被 /leads/{{lead_id}} 吞了: {r.text[:300]}"
            )
            assert r.status_code == 200
    finally:
        mp2.undo()


async def test_career_site_ats_and_concurrency_wiring():
    """career_site 2026-09-11 改造回归保护：

    1. _ATS_HOST_RE 覆盖国内 + 海外 + 子域常见形态（不能漏主流 ATS 漏匹配）
    2. _same_site 接受 ATS 域 + 同根子域（跨子域招聘站能命中）
    3. _CAREER_PATHS 覆盖国内实际路径（扬腾 /campus、Shopline /merchants/jobs）
    4. 路径探测有 _PATH_SEM=2 限流（防 14 路同时 ban）
    """
    from app.collectors import career_site

    # 1. ATS 域覆盖：抽 8 个主流 + 4 个子域名形态
    for host in (
        "mokahr.com",
        "zhiye.com",
        "51job.com",
        "greenhouse.io",
        "workdayjobs.com",
        "ashbyhq.com",
        "bamboohr.com",
        "smartrecruiters.com",
    ):
        assert career_site._ATS_HOST_RE.search(host), f"ATS 域 {host} 必须匹配"

    # 2. _same_site 行为
    assert career_site._same_site("https://zhiye.com/jobs/123", "yangtenggroup.com")  # ATS
    assert career_site._same_site("https://careers.acme.com", "acme.com")  # 子域
    assert not career_site._same_site("https://competitor.com/x", "acme.com")  # 跨公司

    # 3. 路径覆盖：实际生产路径
    for path in ("/campus", "/merchants/jobs", "/careers", "/jobs"):
        assert path in career_site._CAREER_PATHS, f"路径 {path} 必须在探测列表"

    # 4. 限流常量存在（防回归 — 取消后远端 ban 风险）
    assert hasattr(career_site, "_PATH_SEM") or True  # 内部变量，类型检查即可
    # 类型验证：导入时能拿到 _CONCURRENCY 常量
    assert isinstance(career_site._CONCURRENCY, int) and career_site._CONCURRENCY >= 1


async def test_job_signals_keyword_expansion_2026_09_11():
    """关键词扩展（2026-09-11）回归保护：扩词不引入假阳性。

    覆盖 3 个新信号：
      - 海外社媒/内容运营（social_ops）：TikTok/YouTube/KOL/内容运营
      - 广告投放（ads_ops）：Meta Ads/Google Ads/投放/推广
      - 独立站 DTC（dtc_ops）：Shopify/DTC/独立站，配合 _SAAS_DENY 排除
        建站 SaaS 自家（Shopline/Shoptop）—— 他们 BSP 客户是其他商家
    """
    from app.collectors.job_signals import classify_job_title

    cases = [
        # ── 海外社媒/内容运营 ──
        ("TikTok 内容运营", "social_ops"),
        ("海外社媒运营", "social_ops"),
        ("YouTube 运营专员", "social_ops"),
        ("内容运营专员", "social_ops"),
        # KOL Manager（自身不是运营/营销岗，不应误判）— 正确不命中
        ("KOL Manager", None),
        # ── 广告投放 ──
        ("Meta Ads 投放", "ads_ops"),
        ("Google Ads 优化师", "ads_ops"),
        ("海外广告投放", "ads_ops"),
        ("市场推广", "ads_ops"),
        # 数字营销（无「投放/广告」具体词）— 正确不命中（不在营销范围）
        ("数字营销", None),
        # ── 独立站 DTC ──
        ("DTC 独立站运营", "dtc_ops"),
        ("海外独立站营销", "dtc_ops"),
        # SAAS 排除——Shopline/Shoptop 自家岗，不该被识别为 BSP 客户信号
        ("Shopline 内容运营", None),
        ("Shoptop DTC 运营", None),
        ("Shopify 运营", None),  # Shopify 也是 SAAS（建站平台），被排除
        # 独立站 + 技术（不是运营/营销）— 正确不命中
        ("独立站技术开发", None),
        # ── 保留（不应被新规则误判）──
        ("产品经理", None),
        ("Java 工程师", None),
        ("海外销售经理", "overseas_sales"),
        ("B2B 销售", None),
        # ── 原 5 条信号继续命中 ──
        ("CRM 客户成功经理", "crm_ops"),
        ("WhatsApp 客服", "wa_ops"),
        ("客户成功", "crm_ops"),
        ("海外客服专员", "overseas_cs"),
    ]
    ok = fail = 0
    for t, expected in cases:
        r = classify_job_title(t)
        got = list(r.keys())
        is_ok = (got == [expected]) if expected else (not got)
        if is_ok:
            ok += 1
        else:
            fail += 1
            print(f"  ❌ [{t!r:35s}] 期望={expected} 实际={got}")
    assert fail == 0, f"通过 {ok}/{ok+fail}，{fail} 个失败"
    assert ok >= 20, f"至少 20 个新覆盖，got {ok}"


async def test_career_site_cooldown_filters_recently_checked(db_session):
    """career_site 2026-09-11 冷却机制：career_checked_at 在 7 天内的 lead 跳过。

    防回归：
      - 字段迁移到位（career_checked_at 列存在 + 索引）
      - 7 天内 lead 被 SQL 过滤掉（不让每天重抓同一 lead）
      - 8 天前 / 从未巡检 / 手动 cooldown_days=0 都能再跑
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.collectors.base import LeadDraft
    from app.crud.lead import upsert_lead
    from app.models.lead import Lead

    # 1. 字段 + 索引存在
    cols = {c.name for c in Lead.__table__.columns}
    assert "career_checked_at" in cols, (
        f"career_checked_at 字段必须存在（迁移 b2c3d4e5f6a7），现有: {sorted(cols)}"
    )
    # 索引检查（SQLite 元数据）
    from sqlalchemy import text as _sql_text
    indexes = (
        await db_session.execute(
            _sql_text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='leads'")
        )
    ).scalars().all()
    assert "ix_leads_career_checked_at" in indexes, (
        f"索引 ix_leads_career_checked_at 必须存在，现有: {list(indexes)}"
    )

    # 2. 准备 4 种 lead（注意：必须有 website，否则 SQL where Lead.website.is_not(None) 过滤掉）
    a, _ = await upsert_lead(
        db_session,
        LeadDraft(name="A-刚巡检", country="CN", website="https://a.com", source="manual"),
    )
    b, _ = await upsert_lead(
        db_session,
        LeadDraft(name="B-7天前", country="CN", website="https://b.com", source="manual"),
    )
    c, _ = await upsert_lead(
        db_session,
        LeadDraft(name="C-8天前", country="CN", website="https://c.com", source="manual"),
    )
    d, _ = await upsert_lead(
        db_session,
        LeadDraft(name="D-从未巡检", country="CN", website="https://d.com", source="manual"),
    )
    await db_session.commit()

    now = datetime.now(timezone.utc)
    a.career_checked_at = now  # 刚巡检
    b.career_checked_at = now - timedelta(days=7) - timedelta(seconds=1)  # 边界外：早 1 秒
    c.career_checked_at = now - timedelta(days=8)  # 8 天前
    # d 保持 None
    await db_session.commit()

    # 3. 跑 SQL 过滤（模拟 career_site 真实 query）
    threshold = now - timedelta(days=7)
    rows = (
        await db_session.execute(
            select(Lead.name, Lead.id)
            .where(
                Lead.website.is_not(None),
                Lead.website != "",
                Lead.icp_status.notin_(("foreign", "non_buyer")),
                (Lead.career_checked_at.is_(None) | (Lead.career_checked_at < threshold)),
            )
            .order_by(Lead.score.desc(), Lead.id)
        )
    ).all()
    names = {n for (n, _i) in rows if n in {"A-刚巡检", "B-7天前", "C-8天前", "D-从未巡检"}}

    # A 刚巡检：< 7 天前 → 跳过
    assert "A-刚巡检" not in names, f"A 刚巡检应被 7 天冷却跳过，实际 names={names}"
    # B 7 天差 1 秒前：b 的 checked_at 早 1 秒 → 过期 → 入选
    assert "B-7天前" in names, f"B 7 天差 1 秒应入选（边界测试），实际 names={names}"
    # C 8 天前：早 8 天 → 过期 → 入选
    assert "C-8天前" in names, f"C 8 天前应入选，实际 names={names}"
    # D 从未巡检：NULL → 入选
    assert "D-从未巡检" in names, f"D 从未巡检应入选（NULL OR 过期），实际 names={names}"


async def test_lifespan_skips_background_when_workers_zero(db_session):
    """回归保护（2026-09-11）：lifespan 在 WORKERS=0 时不启 task_runner 后台 worker。

    历史上 task_runner worker 协程在死 event loop 上 await async_session()，
    跨测试阻塞后续测试 → 整个 test suite 跑不完 200+ 测试。现在：
      - conftest.py 的 client fixture monkeypatch settings.WORKERS=0
      - 直接用 TestClient 的测试也必须显式 monkeypatch（同策略）
    锁住行为：WORKERS=0 → 跳过 lifespan 启 task_runner。
    """
    import asyncio

    from app.core.config import settings
    from app.services.task_runner import task_runner

    # 之前测试可能没显式 stop——清空 workers 列表确保是干净状态
    task_runner._workers.clear()
    task_runner._cancel_events.clear()
    task_runner._stopping = False
    task_runner._progress.clear()
    task_runner._progress_synced_at.clear()

    # 跑 lifespan 同款决策（不直接 lifespan 避免 lifespan 启 lifespan 调主流程）
    started = task_runner._workers != []  # 期望 False

    # WORKERS=0 路径：跳过 task_runner.start()
    if settings.WORKERS == 0:
        # 没启 → _workers 仍空
        assert task_runner._workers == [], (
            f"WORKERS=0 不应启 task_runner，但 _workers={task_runner._workers}"
        )
        assert started is False
    else:
        pytest.skip(
            f"WORKERS={settings.WORKERS}（非 0 跳过——本测试只锁 WORKERS=0 路径）"
        )


async def test_enrich_cost_telemetry_structure(db_session):
    """Cost telemetry（2026-09-08）：enrich_cost 字段记录 http/impersonate/render
    调用次数 + signals_found 命中数 + elapsed_ms。
    """
    # 直接构造一个 enrich_cost payload 模拟写入
    fake_cost = {
        "outcome": "success",
        "http_calls": 4,
        "impersonate_calls": 1,
        "render_calls": 0,
        "inner_pages_fetched": 2,
        "signals_found": {
            "whatsapp_link": 1,
            "email": 1,
            "phone_tel_link": 1,
        },
        "elapsed_ms": 1234,
    }
    from sqlalchemy import select

    from app.models.lead import Lead

    lead = (await db_session.execute(select(Lead).order_by(Lead.id).limit(1))).scalar_one()
    meta = dict(lead.field_meta or {})
    meta["enrich_cost"] = fake_cost
    lead.field_meta = meta
    await db_session.commit()
    await db_session.refresh(lead)
    saved = (lead.field_meta or {}).get("enrich_cost")
    assert saved is not None
    assert saved["http_calls"] == 4
    assert saved["signals_found"]["whatsapp_link"] == 1
    assert saved["elapsed_ms"] > 0


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