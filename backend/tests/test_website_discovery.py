"""官网发现补全链（2026-08-31）：jobui 等招聘站线索无官网 → 搜索找官网。

共享测试库约束：本文件用 discoverx 前缀唯一域名/公司名，不与其他文件撞。
"""

from app.collectors.base import LeadDraft
from app.collectors.website_enrich import _discover_website, _load_discoverable
from app.crud.lead import upsert_lead


async def test_discover_website_filters_and_normalizes(monkeypatch):
    """搜索结果 → 官网：文章页被过滤、URL 归一为站点根。

    2026-08-31 并行改造后 _discover_website 走 search_with_fallback
    （DDG→必应自动回退），mock 点从 _search 移到该入口。
    """
    from app.collectors import web_search

    async def fake_search(clients, kw, limit, log=None):
        # 第一个：企业站内页（应取，且归一为根）；第二个：内容平台文章（应滤）
        return [
            {"name": "发现链科技（东莞）有限公司 - 官网", "url": "https://www.discoverx-tech.com/products"},
            {"name": "WhatsApp 客服工具选购完整指南", "url": "https://blog.example.com/blog/whatsapp-guide"},
        ], None, "duckduckgo"

    monkeypatch.setattr(web_search, "search_with_fallback", fake_search)
    no_clients = (None, None)  # fake_search 不用 client，仅为签名占位
    ws = await _discover_website(no_clients, "发现链科技（东莞）有限公司")  # type: ignore[arg-type]
    assert ws == "https://discoverx-tech.com"

    async def empty_search(clients, kw, limit, log=None):
        return [], "DDG 不可达", None

    monkeypatch.setattr(web_search, "search_with_fallback", empty_search)
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


def test_discovery_cooldown_negative_cache():
    """发现失败负缓存（2026-08-31 巡检 A 级 bug）：7 天内 miss/dup 标记的线索
    不再进发现窗口——否则失败者分数不变、永远占着前 N 位，后面的线索饿死。"""
    from datetime import datetime, timedelta, timezone

    from app.collectors.website_enrich import _discovery_cooldown

    now = datetime.now(timezone.utc)
    fresh = {"website": {"source": "web_discovery_miss", "updated_at": now.isoformat()}}
    stale = {"website": {"source": "web_discovery_miss",
                         "updated_at": (now - timedelta(days=8)).isoformat()}}
    dup_fresh = {"website": {"source": "web_discovery_dup", "updated_at": now.isoformat()}}
    assert _discovery_cooldown(fresh, now=now) is True
    assert _discovery_cooldown(dup_fresh, now=now) is True
    assert _discovery_cooldown(stale, now=now) is False  # 过冷却期 → 重试
    assert _discovery_cooldown({}, now=now) is False
    assert _discovery_cooldown(None, now=now) is False
    # 成功标记（web_discovery）不是负缓存
    ok = {"website": {"source": "web_discovery", "updated_at": now.isoformat()}}
    assert _discovery_cooldown(ok, now=now) is False


async def test_load_discoverable_skips_cooldown(db_session):
    """冷却中的线索不占发现窗口名额（负缓存接线验证）。"""
    from datetime import datetime, timezone

    from app.crud.lead import touch_field_meta

    from app.collectors.website_enrich import _load_discoverable

    d = LeadDraft(source="job_posting", name="发现链冷却测试科技（深圳）有限公司",
                  country="CN", is_cn=True)
    lead, _ = await upsert_lead(db_session, d)
    touch_field_meta(lead, "website", "web_discovery_miss",
                     confidence=0, now=datetime.now(timezone.utc))
    await db_session.commit()

    ids = [r[0] for r in await _load_discoverable(db_session, 200)]
    assert lead.id not in ids  # 冷却中 → 本轮跳过

    await db_session.delete(lead)
    await db_session.commit()


async def test_domain_taken_dedup_guard(db_session):
    """撞域守卫（2026-08-31 巡检 B 级 bug）：发现链写官网前必查 domain 占用，
    绕过 upsert 去重直接改行会造出永远无人合并的重复线索。"""
    from app.collectors.website_enrich import _domain_taken

    d1 = LeadDraft(source="web_search", name="DiscoverX Holder Ltd",
                   website="https://discoverx-holder.com", country="MY")
    holder, _ = await upsert_lead(db_session, d1)
    await db_session.commit()

    taken = await _domain_taken(db_session, "discoverx-holder.com", exclude_id=holder.id + 999)
    assert taken == holder.id  # 他人持有 → 返回占用者
    assert await _domain_taken(db_session, "discoverx-holder.com", exclude_id=holder.id) is None
    assert await _domain_taken(db_session, "nobody-owns-this-x9z.com", exclude_id=1) is None

    await db_session.delete(holder)
    await db_session.commit()
