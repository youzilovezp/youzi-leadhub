"""官网发现补全链（2026-08-31）：jobui 等招聘站线索无官网 → 搜索找官网。

共享测试库约束：本文件用 discoverx 前缀唯一域名/公司名，不与其他文件撞。
"""

from app.collectors.base import LeadDraft
from app.collectors.website_enrich import _discover_website, _load_discoverable
from app.crud.lead import upsert_lead


async def test_discover_website_filters_and_normalizes(monkeypatch):
    """搜索结果 → 官网：文章页被过滤、URL 归一为站点根、候选站写前验证通过。

    2026-09-01 写前验证（用户红线「联系信息必须爬准」）：候选站首页标题/
    域名必须含公司名特征词才写入——英文公司名的混语搜索第一结果全是
    无关站（实测 nps.gov / aargauhotels.ch 污染）。
    """
    from app.collectors import web_search
    from app.collectors import website_enrich as we

    async def fake_search(clients, kw, limit, log=None):
        # 第一个：企业站内页（应取，且归一为根）；第二个：内容平台文章（应滤）
        return [
            {"name": "发现链科技（东莞）有限公司 - 官网", "url": "https://www.discoverx-tech.com/products"},
            {"name": "WhatsApp 客服工具选购完整指南", "url": "https://blog.example.com/blog/whatsapp-guide"},
        ], None, "duckduckgo"

    async def fake_fetch(clients, url):
        return "<html><head><title>发现链科技（东莞）有限公司 - Home</title></head></html>"

    monkeypatch.setattr(web_search, "search_with_fallback", fake_search)
    monkeypatch.setattr(we, "_fetch_site", fake_fetch)
    no_clients = (None, None)  # 打桩后不用 client，仅为签名占位
    ws = await _discover_website(no_clients, "发现链科技（东莞）有限公司")  # type: ignore[arg-type]
    assert ws == "https://discoverx-tech.com"

    async def empty_search(clients, kw, limit, log=None):
        return [], "DDG 不可达", None

    monkeypatch.setattr(web_search, "search_with_fallback", empty_search)
    assert await _discover_website(no_clients, "任何公司") is None  # type: ignore[arg-type]


async def test_discover_website_verifies_before_write(monkeypatch):
    """写前验证：候选站与公司名零特征重叠 → 否决（不写入错站）。"""
    from app.collectors import web_search
    from app.collectors import website_enrich as we

    queries: list[str] = []

    async def fake_search(clients, kw, limit, log=None):
        queries.append(kw)
        return [
            {"name": "National Park Service (U.S.)", "url": "https://www.nps.gov/"},
            {"name": "Xuchang Yuanxiu Hair - Human Hair Manufacturer", "url": "https://www.discoverx-yuanxiu.com/"},
        ], None, "duckduckgo"

    pages = {
        "https://nps.gov": "<title>National Park Service (U.S. National Park Service)</title>",
        "https://discoverx-yuanxiu.com": "<title>Yuanxiu Hair - Human Hair Bundles Factory</title>",
    }

    async def fake_fetch(clients, url):
        return pages.get(url)

    monkeypatch.setattr(web_search, "search_with_fallback", fake_search)
    monkeypatch.setattr(we, "_fetch_site", fake_fetch)
    monkeypatch.setattr(we, "_DISCOVER_GAP", 0.0)
    no_clients = (None, None)

    ws = await _discover_website(no_clients, "Xuchang Yuanxiu Crafts Co., Ltd")  # type: ignore[arg-type]
    # 英文名用引号精确查询；nps.gov 被否决，第二个候选（yuanxiu 特征词命中）通过
    assert queries[0] == '"Xuchang Yuanxiu Crafts Co., Ltd"'
    assert ws == "https://discoverx-yuanxiu.com"


async def test_discover_website_rejects_all_unrelated(monkeypatch):
    """所有候选都不含公司名特征词 → None（进 7 天负缓存，不写错站）。"""
    from app.collectors import web_search
    from app.collectors import website_enrich as we

    async def fake_search(clients, kw, limit, log=None):
        return [{"name": "Aargau Hotels - Switzerland", "url": "https://www.discoverx-hotels.ch/"}], None, "duckduckgo"

    async def fake_fetch(clients, url):
        return "<title>Aargau Hotels - Official Site</title>"

    vetoed: list[str] = []

    async def fake_log(level, message):
        if "否决" in message:
            vetoed.append(message)

    monkeypatch.setattr(web_search, "search_with_fallback", fake_search)
    monkeypatch.setattr(we, "_fetch_site", fake_fetch)
    monkeypatch.setattr(we, "_DISCOVER_GAP", 0.0)
    assert await _discover_website((None, None), "Beauty (GD) Manufacturing Co., Ltd", log=fake_log) is None  # type: ignore[arg-type]
    assert vetoed, "应留下否决日志"


async def test_discover_website_brand_slug_guess(monkeypatch):
    """品牌域直猜（B2B 线索主路径）：店铺子域 = 品牌名 → {slug}.com 验证命中，
    不再依赖搜索引擎（英文公司名的引擎返回质量不可靠）。"""
    from app.collectors import website_enrich as we

    searched: list[str] = []

    async def fail_search(clients, kw, limit, log=None):
        searched.append(kw)
        return [], "引擎不可达", None

    pages = {
        "https://yuanxiuhair.com": "<title>Yuanxiu Hair - Xuchang Human Hair Factory</title>",
    }

    async def fake_fetch(clients, url):
        return pages.get(url)

    monkeypatch.setattr(we, "_fetch_site", fake_fetch)
    monkeypatch.setattr(we, "_DISCOVER_GAP", 0.0)
    import app.collectors.web_search as ws_mod

    monkeypatch.setattr(ws_mod, "search_with_fallback", fail_search)

    ws = await _discover_website(
        (None, None),  # type: ignore[arg-type]
        "Xuchang Yuanxiu Crafts Co., Ltd",
        brand_slugs=["yuanxiuhair"],
    )
    assert ws == "https://yuanxiuhair.com"
    assert not searched, "品牌域命中后不应再走搜索"


async def test_discover_website_brand_slug_guess_falls_to_search(monkeypatch):
    """品牌域猜不中（域名未注册/被他人持有）→ 回退搜索路径，不误写。"""
    from app.collectors import website_enrich as we
    import app.collectors.web_search as ws_mod

    async def fake_search(clients, kw, limit, log=None):
        return [
            {"name": "Topledvision LED Strip", "url": "https://www.topledvision.com/"},
        ], None, "duckduckgo"

    pages = {
        "https://topledvision.com": "<title>Topledvision Lighting Manufacturer</title>",
    }

    async def fake_fetch(clients, url):
        return pages.get(url)

    monkeypatch.setattr(we, "_fetch_site", fake_fetch)
    monkeypatch.setattr(we, "_DISCOVER_GAP", 0.0)
    monkeypatch.setattr(ws_mod, "search_with_fallback", fake_search)

    ws = await _discover_website(
        (None, None),  # type: ignore[arg-type]
        "Shenzhen Topledvision Lighting Co., Ltd",
        brand_slugs=["topledvision-holdings"],
    )
    assert ws == "https://topledvision.com"


def test_site_title_mentions_token_rules():
    from app.collectors.website_enrich import _site_title_mentions

    # 强特征词（≥5 字符剔通用词）命中
    assert _site_title_mentions("Xuchang Yuanxiu Crafts Co., Ltd", title="Yuanxiu Hair Manufacturer", url="https://yuanxiuhair.com")
    assert _site_title_mentions("Shenzhen Topledvision Lighting Co., Ltd", title="Home", url="https://topledvision.com")
    # 弱词双命中兜底（无 ≥5 特征词时，两个 ≥3 词同时出现）
    assert _site_title_mentions("Shenzhen ATA Technology Co., Ltd", title="Shenzhen ATA Lighting", url="https://ata-led.com")
    # 行业词/通用词不算身份特征：hair/beauty/ltd 单独出现 → 否决
    assert not _site_title_mentions("Juancheng Youzi Hair Products Co., Ltd", title="Milemoa Hair - Best Hair", url="https://milemoa.com")
    assert not _site_title_mentions("Beauty (GD) Manufacturing Co., Ltd", title="Aargau Hotels Official", url="https://aargauhotels.ch")
    # 中文名：相邻汉字对命中（与 site_matches_company 同口径）
    assert _site_title_mentions("宁波凯越国际贸易有限公司", title="凯越国际 - 跨境电商", url="https://example.com")
    assert not _site_title_mentions("宁波凯越国际贸易有限公司", title="完全无关的站点标题", url="https://other.com")
    # 城市词是弱词：单独一个城市词不算（需两个弱词）
    assert not _site_title_mentions("Shenzhen ATA Technology Co., Ltd", title="Shenzhen News Daily", url="https://news.com")


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

    from app.collectors.website_enrich import _load_discoverable
    from app.crud.lead import touch_field_meta

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
