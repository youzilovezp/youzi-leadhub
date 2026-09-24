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
