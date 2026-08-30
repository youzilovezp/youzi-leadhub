"""官网发现补全链（2026-08-31）：jobui 等招聘站线索无官网 → 搜索找官网。

共享测试库约束：本文件用 discoverx 前缀唯一域名/公司名，不与其他文件撞。
"""

from app.collectors.base import LeadDraft
from app.collectors.website_enrich import _discover_website, _load_discoverable
from app.crud.lead import upsert_lead


async def test_discover_website_filters_and_normalizes(monkeypatch):
    """搜索结果 → 官网：文章页被过滤、URL 归一为站点根。"""
    from app.collectors import web_search

    async def fake_search(clients, engine, kw, limit):
        # 第一个：企业站内页（应取，且归一为根）；第二个：内容平台文章（应滤）
        return [
            {"name": "发现链科技（东莞）有限公司 - 官网", "url": "https://www.discoverx-tech.com/products"},
            {"name": "WhatsApp 客服工具选购完整指南", "url": "https://blog.example.com/blog/whatsapp-guide"},
        ], None

    monkeypatch.setattr(web_search, "_search", fake_search)
    no_clients = (None, None)  # fake_search 不用 client，仅为签名占位
    ws = await _discover_website(no_clients, "发现链科技（东莞）有限公司")  # type: ignore[arg-type]
    assert ws == "https://discoverx-tech.com"

    async def empty_search(clients, engine, kw, limit):
        return [], "DDG 不可达"

    monkeypatch.setattr(web_search, "_search", empty_search)
    assert await _discover_website(no_clients, "任何公司") is None  # type: ignore[arg-type]


async def test_load_discoverable_scope(db_session):
    """发现链范围：无官网的非 foreign 线索入选；有官网/foreign 不进。"""
    from app.db.init_db import init_db

    await init_db()
    d1 = LeadDraft(source="job_posting", name="发现链科技（东莞）有限公司", country="CN", is_cn=True)
    lead1, _ = await upsert_lead(db_session, d1)
    d2 = LeadDraft(
        source="meta_ads",
        name="DiscoverX Foreign Commerce",
        country="US",
        website="https://discoverx-foreign.com",
    )
    lead2, _ = await upsert_lead(db_session, d2)
    await db_session.commit()
    # 招聘线索（jobui 公司页无官网字段）→ 待发现；meta_ads 外企有官网且 foreign → 不进
    assert lead1.website is None
    assert lead2.icp_status == "foreign"

    rows = await _load_discoverable(db_session, 200)
    ids = [r[0] for r in rows]
    assert lead1.id in ids
    assert lead2.id not in ids

    # 清理（共享测试库）
    await db_session.delete(lead1)
    await db_session.delete(lead2)
    await db_session.commit()
