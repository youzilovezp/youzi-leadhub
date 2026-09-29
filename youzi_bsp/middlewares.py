"""下载中间件 —— 补 Scrapy 轮子的唯一缺口：内网安全边界（报告 五.6）。

字符串正则挡不住十进制/十六进制 IP 编码（http://2130706433/ → 127.0.0.1）、
IPv4-mapped IPv6、以及解析到私网地址的域名——统一走 stdlib ipaddress +
getaddrinfo 按实际解析结果判定（D2 审计 #2，7 种绕过实测后重写）。

ponytail: getaddrinfo 在 reactor 线程内同步调用 + 结果按 host 缓存——爬虫
规模（千站级、每站一次性解析）足够；DNS rebinding TOCTOU（校验后 Scrapy 再
解析一次）若要封死需接管 DNS 解析层，暂留此上限。
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from scrapy.exceptions import IgnoreRequest

_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 / ::ffff:7f00:1 → 按 v4 判
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip in _CGNAT)


def _blocked_str(addr) -> bool:
    try:
        return _blocked_ip(ipaddress.ip_address(addr))
    except ValueError:
        return True  # 不可解析的地址串按阻断处理


class PrivateNetMiddleware:
    def __init__(self):
        self._verdict: dict[str, bool] = {}  # host → 放行（DNS 解析结果缓存）

    @classmethod
    def from_crawler(cls, crawler):
        return cls()

    def _allowed(self, host: str) -> bool:
        if host in self._verdict:
            return self._verdict[host]
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None
        try:
            ip = ipaddress.ip_address(host)          # 严格点分十进制 / IPv6
        except ValueError:
            ip = None
        if ip is None:
            # 传统编码（十进制/十六/八进制）按 inet_aton 语义解析——注意
            # 不能走 getaddrinfo：macOS 会把 0177.0.0.1 规范成 177.0.0.1 判错
            try:
                ip = ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host)))
            except (OSError, ValueError):
                ip = None
        if ip is not None:
            ok = not _blocked_ip(ip)
        else:
            # 真域名 → 解析全部地址后按实址判
            try:
                infos = socket.getaddrinfo(host, None)
            except socket.gaierror:
                ok = False  # 解析失败直接拒（Scrapy 下载也必然失败）
            else:
                ok = not any(_blocked_str(i[4][0]) for i in infos)
        self._verdict[host] = ok
        return ok

    def process_request(self, request, spider):
        host = (urlsplit(request.url).hostname or "").strip("[]").lower()
        if not host or not self._allowed(host):
            raise IgnoreRequest(f"private/cgnet host blocked: {host}")


# ============================================================================
# HIGH #6：RetryAfterMiddleware —— Scrapy 内置 RetryMiddleware 不读 Retry-After
# 头（spec §五.4 "429/503 尊重 Retry-After 退避"，也是 §7.3 礼貌红线）。
#
# 2026-09-28 重写（审查 P2，双代理独立验证旧实现是死代码）：
# 1. 旧实现往 request.meta["download_delay"] 注入延迟——该 meta 在 Scrapy 2.13
#    下载器重构后全框架无任何读取点（slot delay 只来自 DOWNLOAD_SLOTS/DOWNLOAD_DELAY）；
# 2. 旧优先级 500 < 内置 RetryMiddleware 550，process_response 链按优先级降序执行
#    且返回 Request 即短路——内置先看到 429/503 并重试，旧中间件只在重试耗尽后
#    收到死响应。两重失效 = 429 被原地无延迟立即重试。
#
# 现改为子类化内置重试器并取代之（settings 里把内置置 None）：429/503 在重试前
# 把下载槽 slot.delay 抬到 Retry-After 值（cap 防锁死），重试请求进同一槽自然等待。
# ============================================================================

import email.utils
from datetime import datetime, timezone

from scrapy.downloadermiddlewares.retry import RetryMiddleware


class RetryAfterMiddleware(RetryMiddleware):
    def __init__(self, settings):
        super().__init__(settings)
        self.max_delay = settings.getfloat("RETRY_AFTER_MAX_DELAY", 60)

    @classmethod
    def from_crawler(cls, crawler):
        mw = cls(crawler.settings)
        mw.crawler = crawler
        return mw

    def _parse_retry_after(self, value: str) -> float | None:
        # 优先按秒数解析
        try:
            return max(0.0, float(value))
        except ValueError:
            pass
        # 退路：HTTP 日期（RFC 7231）
        try:
            target = email.utils.parsedate_to_datetime(value)
            if target is None:
                return None
            now = datetime.now(timezone.utc)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return max(0.0, (target - now).total_seconds())
        except (ValueError, TypeError):
            return None

    def process_response(self, request, response, spider=None):
        if response.status in (429, 503):
            ra = response.headers.get("Retry-After")
            if ra is not None:
                try:
                    seconds = self._parse_retry_after(
                        ra.decode("ascii", errors="ignore"))
                except Exception:
                    seconds = None
                if seconds and seconds > 0:
                    # cap 到 max_delay：避免 Retry-After: 86400（一整天）把爬虫锁死
                    self._throttle(request, min(seconds, self.max_delay))
        return super().process_response(request, response, spider)

    def _throttle(self, request, seconds: float) -> None:
        """抬升下载槽延迟——对同槽后续请求（含本响应触发的重试）生效。"""
        key = request.meta.get("download_slot")
        if not key:
            return
        slot = self.crawler.engine.downloader.slots.get(key)
        if slot is not None:
            slot.delay = max(slot.delay, seconds)
