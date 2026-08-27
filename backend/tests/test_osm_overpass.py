"""osm_overpass 采集器测试：查询构建 / 标签映射 / 地理编码 / 镜像退避（全 mock，不打真网）。"""

import asyncio

import httpx
import pytest

from app.collectors.base import TaskContext
from app.collectors.normalize import make_dedupe_key
from app.collectors.osm_overpass import (
    OsmOverpassCollector,
    _MirrorPool,
    build_query,
    osm_to_draft,
    parse_bbox,
)

# ---------- 查询构建（纯函数） ----------


def test_build_query_strict_website():
    q = build_query("3.0,101.5,3.2,101.8", ["dental"], require_website=True, keywords=[])
    # 行业预设 → 标签选择器 union + 联系方式过滤
    assert '["shop"="dentist"]' in q
    assert '[~"^(website|contact:website)$"~"."]' in q
    assert "(3.0,101.5,3.2,101.8)" in q
    assert "[timeout:120]" in q


def test_build_query_multi_industry_union_and_raw_key():
    # 多行业 → union；raw 顶层键兜底可用
    q = build_query("b", ["dental", "health", "amenity"], require_website=True, keywords=[])
    assert q.count("nwr") >= 4  # dental(3 selectors) + health(3) + amenity(1)
    assert '["amenity"]' in q


def test_build_query_empty_industries_fallback():
    q = build_query("b", [], require_website=True, keywords=[])
    assert '["shop"]' in q


def test_build_query_loose_with_keywords():
    q = build_query("b", ["food"], require_website=False, keywords=["dental clinic", "tooth"])
    # 宽松模式含 phone/contact:whatsapp 等联系方式键
    assert "contact:whatsapp" in q
    # 关键词转义 + alternation + 不区分大小写
    assert '~"dental\\ clinic|tooth",i' in q


def test_parse_bbox():
    geo = [{"boundingbox": ["3.1000", "3.2000", "101.6000", "101.7000"]}]
    assert parse_bbox(geo) == "3.1000,101.6000,3.2000,101.7000"
    assert parse_bbox([]) is None


# ---------- 标签映射 ----------


def test_osm_to_draft_full_tags():
    draft = osm_to_draft(
        {
            "name": "Klinik Gigi Ceria",
            "shop": "dentist",
            "website": "https://klinikceria.com",
            "phone": "+60 3-1234 5678",
            "contact:whatsapp": "+60123456789",
            "email": "hi@klinikceria.com",
            "addr:housenumber": "12",
            "addr:street": "Jalan Bukit Bintang",
            "addr:postcode": "55100",
        },
        "MY",
        "Kuala Lumpur",
    )
    assert draft.name == "Klinik Gigi Ceria"
    assert draft.industry == "dentist"
    assert draft.website == "https://klinikceria.com"
    assert draft.phone_raw == "+60 3-1234 5678"
    assert draft.email == "hi@klinikceria.com"
    assert draft.whatsapp_url == "https://wa.me/60123456789"  # 白送的高意向信号
    assert draft.address == "12 Jalan Bukit Bintang, 55100, Kuala Lumpur"
    # 有 website → domain 去重键（不会被社媒键污染）
    key = make_dedupe_key(website=draft.website, phone_raw=draft.phone_raw, region="MY")
    assert key == "domain:klinikceria.com"


def test_osm_to_draft_facebook_website_goes_to_social():
    """website 填了 FB 主页 → 必须归 social，否则 domain:facebook.com 吞掉所有 FB 商家。"""
    draft = osm_to_draft({"name": "Kedai Kopi Ali", "amenity": "cafe", "website": "https://facebook.com/kedaikopiali"}, "MY", "Penang")
    assert draft.website is None
    assert draft.social == {"facebook": "https://facebook.com/kedaikopiali"}
    assert make_dedupe_key(website=draft.website, name=draft.name, city=draft.city) is not None


def test_osm_to_draft_no_name_skipped():
    assert osm_to_draft({"shop": "supermarket"}, "MY", "KL") is None


def test_osm_to_draft_semicolon_multi_value():
    draft = osm_to_draft({"name": "X", "shop": "bakery", "phone": "+60121111111;+60122222222"}, "MY", "KL")
    assert draft.phone_raw == "+60121111111"  # 多值取第一项


def test_osm_to_draft_invalid_whatsapp_not_linked():
    draft = osm_to_draft({"name": "X", "shop": "bakery", "contact:whatsapp": "chat-only"}, "MY", "KL")
    assert draft.whatsapp_url is None  # 解析不出号码宁可不标


# ---------- 参数校验 ----------


@pytest.mark.parametrize(
    ("params", "ok"),
    [
        ({"country": "MY", "cities": "Kuala Lumpur"}, True),
        ({"country": "MYS", "cities": "KL"}, False),  # 非 2 位
        ({"country": "MY"}, False),  # 缺 cities
        ({"country": "MY", "cities": "KL", "categories": "food,zoo"}, False),  # 行业不在预设/白名单
        ({"country": "MY", "cities": "KL", "categories": "dental,shop"}, True),  # 预设 + raw 键混用
    ],
)
def test_validate_params(params, ok):
    if ok:
        OsmOverpassCollector().validate_params(params)
    else:
        from app.core.exceptions import BusinessError

        with pytest.raises(BusinessError):
            OsmOverpassCollector().validate_params(params)


# ---------- 镜像轮换 + 退避（MockTransport，不打真网） ----------


def _make_ctx(logs: list[tuple[str, str]]) -> TaskContext:
    async def emit(d):  # pragma: no cover  本组测试不触发 emit
        return 0, False

    async def log(level, message):
        logs.append((level, message))

    return TaskContext(task_id=0, params={}, emit=emit, log=log, set_total=lambda t: None, inc_progress=lambda d: None)


async def test_fetch_failover_on_429_then_success(monkeypatch):
    """首个镜像 429 → 尊重 Retry-After 后换镜像成功；成功镜像被记住。"""
    sleeps: list[float] = []
    async def fake_sleep(sec):
        sleeps.append(sec)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    calls: list[str] = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, json={"elements": [{"type": "node", "id": 1, "tags": {"name": "A"}}]})

    logs: list[tuple[str, str]] = []
    pool = _MirrorPool()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        elements = await OsmOverpassCollector()._fetch([client], _make_ctx(logs), pool, "[out:json];")
    assert elements is not None and elements[0]["tags"]["name"] == "A"
    assert len(calls) == 2  # 429 后换镜像
    assert calls[0] != calls[1]
    assert sleeps and sleeps[0] == 3.0  # 尊重 Retry-After
    assert any(level == "warn" for level, _ in logs)


async def test_fetch_all_attempts_fail_returns_none(monkeypatch):
    async def fake_sleep(sec):
        pass
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(504)

    logs: list[tuple[str, str]] = []
    pool = _MirrorPool()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await OsmOverpassCollector()._fetch([client, client], _make_ctx(logs), pool, "[out:json];") is None
    assert len([m for _, m in logs if "504" in m]) == 6  # 每次尝试（3 镜像 × 2 通道）都有日志


def test_mirror_pool_rotation():
    pool = _MirrorPool(("a", "b", "c"))
    assert [pool.next() for _ in range(4)] == ["a", "b", "c", "a"]
    pool.mark_good("c")
    assert pool.next() == "c"  # 记住好用的实例
