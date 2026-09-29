"""PrivateNetMiddleware 回归：D2 审计 #2 的 7 种绕过必须全被拦（无网络依赖）。"""
import pytest
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Request

from youzi_bsp.middlewares import PrivateNetMiddleware

# 审计实测的绕过姿势：十进制/十六/八进制 IP 编码、IPv4-mapped IPv6、
# 链路本地 IPv6、CGNAT（云元数据段），加上经典私网段
BLOCKED = [
    "http://2130706433/",                      # 十进制 127.0.0.1
    "http://0x7f.0.0.1/",                      # 十六进制
    "http://0177.0.0.1/",                      # 八进制
    "http://[::ffff:127.0.0.1]/",              # IPv4-mapped（点分）
    "http://[::ffff:7f00:1]/",                 # IPv4-mapped（十六）
    "http://[fe80::1]/",                       # 链路本地 IPv6
    "http://100.100.100.200/",                 # CGNAT（阿里云元数据段）
    "http://localhost/",
    "http://127.0.0.1/",
    "http://10.0.0.5/",
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "http://169.254.169.254/latest/meta-data",
    "http://0.0.0.0/",
]


@pytest.mark.parametrize("url", BLOCKED)
def test_blocked(url):
    with pytest.raises(IgnoreRequest):
        PrivateNetMiddleware().process_request(Request(url), None)


def test_public_ip_allowed():
    PrivateNetMiddleware().process_request(Request("http://8.8.8.8/"), None)  # 不抛即过


def test_unresolvable_host_blocked():
    # 解析失败按阻断处理（Scrapy 下载同样必然失败）
    with pytest.raises(IgnoreRequest):
        PrivateNetMiddleware().process_request(
            Request("http://this-host-does-not-exist-zz.invalid/"), None)


# ============================================================================
# HIGH #6（2026-09-28 重写）: RetryAfterMiddleware = RetryMiddleware 子类。
# 旧实现往 request.meta["download_delay"] 注入——Scrapy 2.13 后该 meta 全框架
# 无读取点（死代码），且优先级 500 < 内置 550 被 process_response 链短路。
# 现断言真实行为：429/503 先抬下载槽 slot.delay 再重试。
# ============================================================================

from types import SimpleNamespace

from scrapy.http import Response
from scrapy.settings import Settings

from youzi_bsp.middlewares import RetryAfterMiddleware


class _Slot:
    def __init__(self, delay=0.0):
        self.delay = delay


def _mw(slot_delay=0.0):
    from scrapy.statscollectors import StatsCollector

    crawler = SimpleNamespace(
        settings=Settings(),
        stats=None,                      # 占位，建好后回填（StatsCollector 要 crawler）
        spider=None,
        engine=SimpleNamespace(downloader=SimpleNamespace(
            slots={"x.com": _Slot(slot_delay)})),
    )
    crawler.stats = StatsCollector(crawler)  # get_retry_request 走 spider.crawler.stats
    crawler.settings.set("RETRY_HTTP_CODES", [429, 500, 502, 503, 504],
                         priority="cmdline")
    crawler.spider = SimpleNamespace(crawler=crawler)
    return RetryAfterMiddleware.from_crawler(crawler)


def _resp_with_retry_after(value, status=503):
    return Response(url="http://x.com/", status=status,
                    headers=({"Retry-After": value} if value else {}), body=b"")


def _req():
    return Request("http://x.com/page2", meta={"download_slot": "x.com"})


def test_retry_after_raises_slot_delay_and_retries():
    """503 + Retry-After: 5（数字）→ slot.delay=5 且产出重试 Request（retry_times=1）。"""
    mw = _mw()
    out = mw.process_response(_req(), _resp_with_retry_after("5"), None)
    assert mw.crawler.engine.downloader.slots["x.com"].delay == 5.0
    # 内置重试语义保留：可重试状态返回重试 Request 而非原响应
    assert isinstance(out, Request) and out.meta["retry_times"] == 1


def test_retry_after_429_also_throttled():
    """429 同样在重试码里（RETRY_HTTP_CODES 显式含 429）。"""
    mw = _mw()
    out = mw.process_response(_req(), _resp_with_retry_after("7", status=429), None)
    assert mw.crawler.engine.downloader.slots["x.com"].delay == 7.0
    assert isinstance(out, Request)


def test_retry_after_http_date():
    """503 + Retry-After: HTTP 日期 → slot.delay ≈ 距今秒数。"""
    from datetime import datetime, timedelta, timezone

    future = (datetime.now(timezone.utc)
              + timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    mw = _mw()
    mw.process_response(_req(), _resp_with_retry_after(future), None)
    assert 0 < mw.crawler.engine.downloader.slots["x.com"].delay <= 31


def test_retry_after_no_header_still_retries_without_throttle():
    """无 Retry-After → 不抬 delay，但内置重试照常发生。"""
    mw = _mw()
    out = mw.process_response(_req(), _resp_with_retry_after(None), None)
    assert mw.crawler.engine.downloader.slots["x.com"].delay == 0.0
    assert isinstance(out, Request)


def test_retry_after_2xx_passes_through():
    """2xx 原样返回，delay 不动。"""
    mw = _mw()
    res = _resp_with_retry_after("5", status=200)
    out = mw.process_response(_req(), res, None)
    assert out is res
    assert mw.crawler.engine.downloader.slots["x.com"].delay == 0.0


def test_retry_after_caps_to_max():
    """Retry-After 3600（1 小时）→ cap 到 60s，避免占住爬虫。"""
    mw = _mw()
    mw.process_response(_req(), _resp_with_retry_after("3600"), None)
    assert mw.crawler.engine.downloader.slots["x.com"].delay == 60.0


def test_retry_after_never_lowers_existing_delay():
    """槽已有更长延迟时不得回降（max 语义，防已抬的延迟被冲掉）。"""
    mw = _mw(slot_delay=10.0)
    mw.process_response(_req(), _resp_with_retry_after("3"), None)
    assert mw.crawler.engine.downloader.slots["x.com"].delay == 10.0


def test_retry_exhausted_returns_response():
    """重试预算耗尽（RETRY_TIMES=2）→ 返回响应本身，重试链正确终止。"""
    mw = _mw()
    first = mw.process_response(_req(), _resp_with_retry_after("1"), None)
    second = mw.process_response(first, _resp_with_retry_after("1"), None)
    third = mw.process_response(second, _resp_with_retry_after("1"), None)
    assert isinstance(first, Request) and isinstance(second, Request)
    assert not isinstance(third, Request)   # 第 3 次（retry_times=3 > 2）放弃
