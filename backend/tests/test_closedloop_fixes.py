"""2026-08-31 深度巡检修复的回归测试：

- last_ad_at 列接线（此前无写入方恒空）：meta_ads draft → upsert 取 max 合并
- export_type 列接线（此前无写入方恒空）：apply_score 与评分/ICP 同点派生

共享测试库约束：本文件全部用 closedloop 前缀的唯一域名，不与其他文件撞。
"""

from datetime import datetime, timezone
from typing import Any

import pytest_asyncio

from app.collectors.base import LeadDraft
from app.collectors.overseas import derive_export_type
from app.crud.lead import upsert_lead

_T1 = datetime(2026, 8, 1, tzinfo=timezone.utc)
_T2 = datetime(2026, 8, 20, tzinfo=timezone.utc)


@pytest_asyncio.fixture(autouse=True)
async def _ensure_schema():
    """单文件运行兜底：db_session 测试不经过 client fixture，这里确保表已建
    （init_db 幂等；全会话共享同一 SQLite 文件，其他文件先跑时是空操作）。"""
    from app.db.init_db import init_db

    await init_db()


def test_derive_export_type_rules():
    """出海业务类型四形态 + 无出海证据不硬分类。"""
    # 无出海证据（cn_domestic/foreign 形态）→ None
    assert derive_export_type() is None
    assert derive_export_type(industry="电子") is None
    # 电商栈 / 电商行业（需先有出海证据）→ 跨境电商
    assert derive_export_type(overseas_signals={"ecommerce": ["shopify"]}) == "跨境电商"
    assert derive_export_type(industry="美妆", target_countries=["US"]) == "跨境电商"
    # 多语言 / 多市场 / 在投广告 / 出海自述 → 品牌出海
    assert derive_export_type(overseas_signals={"languages": ["en", "de"]}) == "品牌出海"
    assert derive_export_type(overseas_signals={"markets": ["US", "GB"]}) == "品牌出海"
    assert derive_export_type(sources=[{"source": "meta_ads"}]) == "品牌出海"
    assert derive_export_type(overseas_signals={"export_words": ["global supplier"]}) == "品牌出海"
    # 仅海外岗位：客服/销售 → 出海服务；社媒/WA 运营 → 出海营销
    assert derive_export_type(job_signals={"overseas_cs": {"label": "海外客服", "points": 20}}) == "出海服务"
    assert derive_export_type(job_signals={"overseas_sales": {"label": "海外销售", "points": 10}}) == "出海服务"
    assert derive_export_type(job_signals={"social_ops": {"label": "海外社媒运营", "points": 15}}) == "出海营销"
    assert derive_export_type(job_signals={"wa_ops": {"label": "WhatsApp 运营", "points": 30}}) == "出海营销"


async def test_upsert_last_ad_at_and_export_type(db_session):
    """last_ad_at 合并取 max；export_type 随 apply_score 派生（行属性一致）。"""
    lead, created = await upsert_lead(
        db_session,
        LeadDraft(
            source="meta_ads",
            name="闭环修复科技（深圳）有限公司",
            website="https://closedloop-fix.com",
            country="CN",
            is_cn=True,
            ad_count=3,
            last_ad_at=_T1,
        ),
    )
    assert created
    assert lead.last_ad_at == _T1
    # meta_ads 来源 → 有出海证据 → export_type=品牌出海（在投广告口径）
    assert lead.export_type == "品牌出海"

    # 同一企业（同 domain）再来更新时间更晚的广告 draft → last_ad_at 取 max
    merged, created2 = await upsert_lead(
        db_session,
        LeadDraft(
            source="meta_ads",
            name="闭环修复科技（深圳）有限公司",
            website="https://closedloop-fix.com",
            ad_count=5,
            last_ad_at=_T2,
        ),
    )
    assert not created2 and merged.id == lead.id
    assert merged.last_ad_at == _T2

    # 更早的时间不来覆盖（只增不减语义）
    merged2, _ = await upsert_lead(
        db_session,
        LeadDraft(
            source="meta_ads",
            name="闭环修复科技（深圳）有限公司",
            website="https://closedloop-fix.com",
            ad_count=1,
            last_ad_at=_T1,
        ),
    )
    assert merged2.last_ad_at == _T2

    await db_session.delete(merged2)
    await db_session.commit()


async def test_upsert_auto_contact_from_collector_email(db_session):
    """找谁（2026-08-31 巡检）：采集器 draft 带邮箱 → 自动生成「待补全」联系人
    并重评（联系人维）；手工录入（source=manual）不自动生成。"""
    from sqlalchemy import select

    from app.models.lead import LeadContact

    draft_kw: dict[str, Any] = {
        "source": "meta_ads",
        "name": "闭环联系人科技（厦门）有限公司",
        "website": "https://closedloop-contact.com",
        "country": "CN",
        "is_cn": True,
        "email": "sales@closedloop-contact.com",
    }
    lead, created = await upsert_lead(db_session, LeadDraft(**draft_kw))
    assert created
    contacts = (
        await db_session.execute(select(LeadContact).where(LeadContact.lead_id == lead.id))
    ).scalars().all()
    assert len(contacts) == 1
    assert contacts[0].email == "sales@closedloop-contact.com"
    assert contacts[0].source == "meta_ads"  # 来源透传 draft.source（2026-08-31 审计口径）
    assert contacts[0].job_title is None  # 「待补全」

    # 同邮箱再合并不重复生成
    merged, created2 = await upsert_lead(db_session, LeadDraft(**draft_kw))
    assert not created2
    contacts2 = (
        await db_session.execute(select(LeadContact).where(LeadContact.lead_id == lead.id))
    ).scalars().all()
    assert len(contacts2) == 1

    # 手工录入带邮箱 → 不自动建联系人（用户自己维护）
    manual, created3 = await upsert_lead(
        db_session,
        LeadDraft(
            source="manual",
            name="闭环手工录入科技（福州）有限公司",
            website="https://closedloop-manual.com",
            email="hi@closedloop-manual.com",
        ),
    )
    assert created3
    manual_contacts = (
        await db_session.execute(select(LeadContact).where(LeadContact.lead_id == manual.id))
    ).scalars().all()
    assert manual_contacts == []

    # 清理（共享测试库）：SQLite 无级联，联系人显式删——否则孤儿联系人会在
    # 行 id 复用时污染后续测试的联系人维评分（test_upsert_merge 实测踩中）
    from sqlalchemy import delete as sa_delete

    await db_session.execute(
        sa_delete(LeadContact).where(LeadContact.lead_id.in_([merged.id, manual.id]))
    )
    await db_session.delete(merged)
    await db_session.delete(manual)
    await db_session.commit()


async def test_maybe_chain_enrich_creates_and_throttles(db_session, monkeypatch):
    """采集流水线自动接力（2026-08-31 交互改造）：发现类完成 → 自动排入富化；
    去重只认「全库富化还在排队」（排队中的扫描必然覆盖新增线索），刚跑完的
    不算数——按时间窗口节流会整批漏富化（同日修复）；非发现类/开关关 → 不动。"""
    from datetime import datetime, timezone

    from sqlalchemy import delete as sa_delete
    from sqlalchemy import select as sa_select
    from sqlalchemy import update as sa_update

    from app.models.collect_task import CollectTask
    from app.services.task_runner import task_runner

    logs: list[tuple[str, str]] = []

    async def log_fn(level: str, msg: str) -> None:
        logs.append((level, msg))

    # 共享测试库可能有别的用例留下的隐式任务——只数本用例接力产生的
    # （名字带「自动富化复核（由 … 任务 #99xx」），用基线差值断言
    _CHAIN_NAME = "🔁 自动富化复核（由 "

    async def _chained_count() -> int:
        rows = (
            await db_session.execute(
                sa_select(CollectTask.id).where(CollectTask.name.like(f"{_CHAIN_NAME}%"))
            )
        ).all()
        return len(rows)

    baseline = await _chained_count()

    # 1) 发现类采集器（web_search）→ 创建隐式富化任务并入队
    await task_runner._maybe_chain_enrich(log_fn, 9901, "web_search", 1)
    assert await _chained_count() == baseline + 1
    chained = (
        (
            await db_session.execute(
                sa_select(CollectTask).where(CollectTask.name.like("%任务 #9901%"))
            )
        )
        .scalars()
        .all()
    )
    assert len(chained) == 1
    assert chained[0].collector == "website_enrich"
    assert chained[0].is_implicit
    assert any("已自动接力" in m for _, m in logs)

    # 2) 非发现类（website_enrich 自己）→ 完全不动
    logs.clear()
    await task_runner._maybe_chain_enrich(log_fn, 9902, "website_enrich", 1)
    assert not logs
    assert await _chained_count() == baseline + 1

    # 清掉 1) 排队中的接力任务（新去重逻辑下它会挡住后续用例的接力）
    await db_session.delete(chained[0])
    await db_session.commit()

    # 3) 富化刚跑完（旧时间窗口的节流条件）→ 必须照常接力：刚跑完的富化
    #    扫不到本批新增线索，按"跑没跑过"节流会整批漏富化（2026-08-31 修复）
    ran = CollectTask(
        name="最近的富化（节流桩）", collector="website_enrich", params={},
        status="completed", last_run_at=datetime.now(timezone.utc),
    )
    db_session.add(ran)
    await db_session.commit()
    logs.clear()
    await task_runner._maybe_chain_enrich(log_fn, 9903, "job_posting", 1)
    assert any("已自动接力" in m for _, m in logs)
    assert await _chained_count() == baseline + 1  # 刚跑过 ≠ 覆盖，必须补接力

    # 4) 全库富化（params 空）排队中 → 跳过（它扫得到本批新增）
    logs.clear()
    await task_runner._maybe_chain_enrich(log_fn, 9905, "web_search", 1)
    assert any("跳过自动富化接力" in m for _, m in logs)
    assert await _chained_count() == baseline + 1  # 没有新建

    # 5) 勾选型富化（lead_ids）排队 → 不算数，照常接力（它不扫全库）
    picked = CollectTask(
        name="勾选富化（节流桩）", collector="website_enrich",
        params={"lead_ids": [1, 2]}, status="queued",
    )
    db_session.add(picked)
    # 顺手把 3) 接力的那个消费掉（置 completed），隔离 5) 的判定
    await db_session.execute(
        sa_update(CollectTask).where(CollectTask.name.like("%任务 #9903%")).values(status="completed")
    )
    await db_session.commit()
    logs.clear()
    await task_runner._maybe_chain_enrich(log_fn, 9906, "meta_ads", 1)
    assert any("已自动接力" in m for _, m in logs)
    assert await _chained_count() == baseline + 2

    # 6) 开关关闭 → 不动
    monkeypatch.setattr("app.core.config.settings.AUTO_CHAIN_ENRICH", False)
    logs.clear()
    await task_runner._maybe_chain_enrich(log_fn, 9907, "meta_ads", 1)
    assert not logs
    assert await _chained_count() == baseline + 2

    # 清理（共享测试库）：只删本用例接力产生的 + 节流桩
    await db_session.execute(
        sa_delete(CollectTask).where(CollectTask.name.like(f"{_CHAIN_NAME}%"))
    )
    await db_session.delete(ran)
    await db_session.delete(picked)
    await db_session.commit()


def test_portal_domains_filtered():
    """门户/内容社区不进线索池（sohu.com 实测被当企业官网入库后的修复）。"""
    from app.collectors.web_search import _is_company_site

    for domain in (
        "sohu.com", "www.sohu.com", "zhihu.com", "36kr.com", "csdn.net",
        "weibo.com", "xiaohongshu.com", "bilibili.com",
    ):
        assert not _is_company_site(f"https://{domain}/some/article"), domain
    # 正常企业站不受影响
    assert _is_company_site("https://salesmartly.com/")
    assert _is_company_site("https://www.acme-trading.com/about")


def test_detect_tel_phones():
    """tel: 链接电话提取（富化补联系方式的直接来源）。"""
    from app.collectors.website_enrich import detect_tel_phones

    html = '<a href="tel:+86 755-1234 5678">call</a> <a href="tel:075512345678">call2</a>'
    assert detect_tel_phones([html]) == ["+8675512345678", "075512345678"]
    assert detect_tel_phones(["<p>no tel here</p>"]) == []
    assert detect_tel_phones(["", None]) == []
