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


async def _collect_start(sp):
    return [r async for r in sp.start()]
