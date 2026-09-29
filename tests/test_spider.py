"""WaSpider 页面预算回归：D2 审计 #1——首页联系链接数不得超过每站预算。"""
from scrapy.http import HtmlResponse, Request

from youzi_bsp.spider import WaSpider

# 首页 8 个不同联系链接 + 2 个归一化后重复的变体（尾斜杠/UTM）
_LINKS = [f"https://x.com/contact{i}" for i in range(8)] + [
    "https://x.com/contact0/", "https://x.com/contact1?utm_source=x"]
HTML = "<html><body>" + "".join(f'<a href="{u}">c</a>' for u in _LINKS) + "</body></html>"


def _resp(url, html):
    r = HtmlResponse(url=url, body=html.encode(), encoding="utf-8")
    r.request = Request(url)  # response.meta 读 request.meta
    return r


def test_budget_enforced_on_scheduled():
    sp = WaSpider(max_pages=3)
    out = list(sp.parse(_resp("https://x.com/", HTML)))
    reqs = [o for o in out if isinstance(o, Request)]
    items = [o for o in out if isinstance(o, dict)]
    assert len(reqs) == 2                      # 首页 + 2 = max_pages 3，不是全量 8
    assert len(items) == 1 and items[0]["channel"] == "sample"


def test_budget_dedups_normalized_duplicates():
    sp = WaSpider(max_pages=10)
    reqs = [o for o in sp.parse(_resp("https://x.com/", HTML))
            if isinstance(o, Request)]
    urls = [r.url for r in reqs]
    assert len(urls) == len(set(urls))         # contact0/ 尾斜杠变体归一后去重


def test_budget_one_means_homepage_only():
    sp = WaSpider(max_pages=1)
    reqs = [o for o in sp.parse(_resp("https://x.com/", HTML))
            if isinstance(o, Request)]
    assert reqs == []


def test_errback_yields_error_item():
    """失败种子（robots 拒/DNS/超时/HttpError）经 errback 产出带 error 标记的 item。"""
    from twisted.python.failure import Failure

    f = Failure(Exception("dns"))  # scrapy 会先给 Failure 挂 .request（scraper.py）
    f.request = Request("https://x.com/", meta={"seed_host": "x.com"})
    item = list(WaSpider()._fail(f))[0]
    assert item == {"url": "https://x.com/", "channel": "sample",
                    "seed_host": "x.com", "error": True}


def test_start_yields_errback_and_meta():
    """Scrapy ≥2.13 引擎只调 start()：默认实现是裸 Request（无 errback/meta），
    种子失败会被静默丢弃——必须由 WaSpider.start() 显式带上。"""
    import asyncio

    sp = WaSpider()
    sp.start_urls = ["https://x.com/"]
    reqs = asyncio.run(_collect_start(sp))
    assert len(reqs) == 1
    assert reqs[0].errback == sp._fail
    assert reqs[0].meta["seed_host"] == "x.com"


def test_seed_file_with_developer_name():
    """CRIT #1 集成：seed 文件 `URL<TAB>developer_name` 解析后透传到 item meta。"""
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("https://brand.example.io/\t深圳市某某科技有限公司\n")
        f.write("https://plain.example.io/\n")  # 无 developer_name
        path = f.name
    sp = WaSpider(seed_file=path, channel="play")
    assert sp._dev_names.get("https://brand.example.io/") == "深圳市某某科技有限公司"
    assert "https://plain.example.io/" not in sp._dev_names
    req = sp._request("https://brand.example.io/", "brand.example.io")
    assert req.meta.get("developer_name") == "深圳市某某科技有限公司"


async def _collect_start(sp):
    return [r async for r in sp.start()]


# ============================================================================
# HIGH #10: _pages_per_host 改按 entity_key 而非 host 计预算
# ============================================================================

def test_budget_by_entity_key_cross_subdomain():
    """HIGH #10 修复：example.com → www.example.com 重定向后预算应共享
    （entity_key 都是 example.com）。

    旧实现按 host 计：a.com 重定向到 www.a.com 后被识别为新 host，`_pages_per_host`
    重新计 1 → 一站实际爬 5+5=10 页，破 ≤5 页承诺。
    """
    sp = WaSpider(max_pages=3)
    # 第一次：example.com 上发现 1 个联系链接 → 调度 1
    out1 = list(sp.parse(_resp(
        "https://example.com/",
        '<a href="https://www.example.com/contact">c</a>')))
    assert sum(isinstance(o, Request) for o in out1) == 1

    # 第二次：模拟重定向到 www.example.com/about。
    # OLD 行为：host="www.example.com" ≠ "example.com"，新 host 计 = 2（首次）。
    # → 触发首页联系页发现分支 → 会再调度联系页（破预算）。
    # NEW 行为：entity_key("www.example.com") == entity_key("example.com")，
    # 该实体已访问过 → 不再提取联系页。
    out2 = list(sp.parse(_resp(
        "https://www.example.com/about",
        '<a href="https://www.example.com/team">t</a>')))
    reqs2 = [o for o in out2 if isinstance(o, Request)]
    assert reqs2 == [], "同 entity_key 应共享预算，重定向后不再发现联系页"


# ============================================================================
# B3（2026-09-28）: Sitemap 兜底 —— 首页无联系语义链接时走 robots.txt（报告 3.3）
# ============================================================================

ROBOTS = b"User-agent: *\nDisallow: /private\nSitemap: https://x.com/sitemap.xml\n"
SITEMAP = (b"<urlset>"
           b"<url><loc>https://x.com/contact-us</loc></url>"
           b"<url><loc>https://x.com/shop/item-1</loc></url>"
           b"<url><loc>https://cdn.other.net/contact</loc></url>"
           b"</urlset>")


def _plain_resp(url, body, meta=None):
    r = HtmlResponse(url=url, body=body, encoding="utf-8")
    r.request = Request(url, meta=meta or {})
    return r


def test_no_contact_links_triggers_robots_fallback():
    """首页无联系语义链接 → 请求 robots.txt（dont_filter：RobotsTxtMiddleware
    已以同 URL 抓过，dupefilter 会拦掉普通请求）。"""
    sp = WaSpider(max_pages=3)
    reqs = [o for o in sp.parse(_resp("https://x.com/",
                                      "<html><body>plain</body></html>"))
            if isinstance(o, Request)]
    assert [r.url for r in reqs] == ["https://x.com/robots.txt"]
    assert reqs[0].dont_filter is True
    assert "x.com" in sp._sitemap_tried      # 同实体不重复触发


def test_contact_links_present_skips_fallback():
    """首页挖到联系链接就不走兜底（预算留给直链）。"""
    sp = WaSpider(max_pages=3)
    reqs = [o for o in sp.parse(_resp("https://x.com/", HTML))
            if isinstance(o, Request)]
    assert all(not r.url.endswith("robots.txt") for r in reqs)


def test_max_pages_one_skips_fallback():
    """max_pages=1 联系页预算为 0，兜底挖到也入不了队——不浪费请求。"""
    sp = WaSpider(max_pages=1)
    reqs = [o for o in sp.parse(_resp("https://x.com/",
                                      "<html><body>plain</body></html>"))
            if isinstance(o, Request)]
    assert reqs == []


def test_robots_to_sitemap_to_contact_page():
    """完整链：robots Sitemap 行 → sitemap.xml → 只入队同实体联系语义 URL。"""
    sp = WaSpider(max_pages=3)
    sp.parse(_resp("https://x.com/", "<html><body>plain</body></html>"))
    sm_reqs = list(sp.parse_robots(_plain_resp(
        "https://x.com/robots.txt", ROBOTS,
        meta={"_entity": "x.com", "seed_host": "x.com"})))
    assert [r.url for r in sm_reqs] == ["https://x.com/sitemap.xml"]

    out = list(sp.parse_sitemap(_plain_resp(
        "https://x.com/sitemap.xml", SITEMAP, meta=sm_reqs[0].meta)))
    urls = [r.url for r in out if isinstance(r, Request)]
    # 跨域 cdn.other.net 不追（严格同实体）；shop/item 无联系语义不入队
    assert urls == ["https://x.com/contact-us"]


def test_robots_without_sitemap_line_tries_default():
    """robots 未声明 Sitemap → 试约定俗成的 /sitemap.xml。"""
    sp = WaSpider(max_pages=3)
    sm_reqs = list(sp.parse_robots(_plain_resp(
        "https://x.com/robots.txt", b"User-agent: *\n",
        meta={"_entity": "x.com", "seed_host": "x.com"})))
    assert sm_reqs[0].url == "https://x.com/sitemap.xml"


def test_sitemap_index_descends_one_level():
    """sitemap index（loc 全是 .xml 且没挖到联系页）→ 下钻最多 2 个、一层。"""
    idx = (b"<sitemapindex>"
           b"<sitemap><loc>https://x.com/sitemap-posts.xml</loc></sitemap>"
           b"</sitemapindex>")
    sp = WaSpider(max_pages=3)
    out = list(sp.parse_sitemap(_plain_resp(
        "https://x.com/sitemap.xml", idx,
        meta={"_entity": "x.com", "_sm_depth": 0})))
    assert [r.url for r in out] == ["https://x.com/sitemap-posts.xml"]
    # 下钻一层后不再递归
    out2 = list(sp.parse_sitemap(_plain_resp(
        "https://x.com/sitemap-posts.xml", idx,
        meta={"_entity": "x.com", "_sm_depth": 1})))
    assert out2 == []
