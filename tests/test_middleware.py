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
    "http://[::ffff:7f00:1]/",                 # IPv4-mapped（十六进制）
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
