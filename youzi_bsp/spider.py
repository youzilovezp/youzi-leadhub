"""WaSpider：首页 → 同域联系语义链接，每站 ≤5 页（报告 3.3 页面发现）。

不抓固定英文路径（/hubungi-kami、/kontakt 等本地化路径），而是从 nav/header/footer
提取联系语义链接；严格同域（防顺着第三方表单/社链爬出站外）。
"""
from __future__ import annotations

from collections import defaultdict
from urllib.parse import urlsplit

from scrapy import Request
from scrapy.linkextractors import LinkExtractor
from scrapy.spiders import Spider

from youzi_bsp.normalize import normalize_url

# 多语联系语义（报告 3.3）：英文/德/西/印尼/越南/阿语常见"联系"词根
CONTACT_WORDS = (
    r"contact|kontakt|kontak|contacto|contacte|hubungi|lien-he|lienhe|"
    r"اتصل|impressum|reach|about-us|aboutus|nosotros|sobre"
)


class WaSpider(Spider):
    name = "wa"

    contact_le = LinkExtractor(allow=(CONTACT_WORDS,))

    def __init__(self, seed_file=None, channel="sample", limit=1000, max_pages=5,
                 *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.channel = channel
        self.max_pages = int(max_pages)
        self._pages_per_host: dict[str, int] = defaultdict(int)
        self._scheduled: dict[str, int] = defaultdict(int)
        urls: list[str] = []
        if seed_file:
            with open(seed_file, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    u = normalize_url(line if "://" in line else f"https://{line}")
                    if u:
                        urls.append(u)
        self.start_urls = urls[: int(limit)]
        if not self.start_urls:
            self.logger.warning("seed_file 为空或全部非法: %s", seed_file)

    async def start(self):
        """Scrapy ≥2.13 引擎入口。必须重写：默认实现只 yield 裸 Request
        （无 errback/meta）——种子失败会被静默丢弃，error 留痕全部失效。"""
        for u in self.start_urls:
            yield self._request(u, urlsplit(u).hostname)

    def start_requests(self):
        # 兼容 <2.13 旧引擎（2.13+ 只调 start()）
        for u in self.start_urls:
            yield self._request(u, urlsplit(u).hostname)

    def parse(self, response):
        host = (urlsplit(response.url).hostname or "").lower()
        # Scrapy 2.19 兼容层可能不保留 start_requests 的 meta，容错读取
        seed_host = response.meta.get("seed_host") or host
        self._pages_per_host[host] += 1
        # 仅首页（每站第 1 页）发现后续联系页——预算集中在最可能有号码的页面
        if self._pages_per_host[host] == 1:
            seen: set[str] = set()
            for link in self.contact_le.extract_links(response):
                target = normalize_url(link.url)
                if not target or target in seen:
                    continue
                seen.add(target)
                thost = (urlsplit(target).hostname or "").lower()
                # 严格同域 + 每站预算（含首页共 max_pages 页，按已调度数计，
                # 不能按已下载数——调度发生在首页解析时，已下载数恒为 1）
                if thost == host and self._scheduled[host] < self.max_pages - 1:
                    self._scheduled[host] += 1
                    yield self._request(target, seed_host)
        yield {
            "url": response.url,
            "html": response.text,
            "channel": self.channel,
            "seed_host": seed_host,
        }

    def _request(self, url: str, seed_host: str | None) -> Request:
        # 不过 dont_filter：交给 Scrapy dupefilter 去重（JOBDIR 续跑时也持久化）
        return Request(url, callback=self.parse, errback=self._fail,
                       meta={"seed_host": seed_host})

    def _fail(self, failure):
        """errback 统一入口：robots 拒/私网拦(IgnoreRequest)、DNS/超时、HttpError。
        失败种子也产出 item 落库留痕，否则 hit_rate 分母只含成功域（P1）。"""
        req = failure.request
        yield {
            "url": req.url,
            "channel": self.channel,
            "seed_host": req.meta.get("seed_host"),
            "error": True,
        }
