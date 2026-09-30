"""WaSpider：首页 → 同域联系语义链接，每站 ≤5 页（报告 3.3 页面发现）。

不抓固定英文路径（/hubungi-kami、/kontakt 等本地化路径），而是从 nav/header/footer
提取联系语义链接；严格同域（防顺着第三方表单/社链爬出站外）。
首页一个联系链接都没挖到时，走 robots.txt Sitemap 兜底（报告 3.3）。
"""
from __future__ import annotations

import html as _html
import re
from collections import defaultdict
from urllib.parse import urlsplit

from scrapy import Request
from scrapy.linkextractors import LinkExtractor
from scrapy.spiders import Spider

from app.normalize import entity_key, normalize_url

# 多语联系语义（报告 3.3）：英文/德/西/印尼/越南/阿语常见"联系"词根
CONTACT_WORDS = (
    r"contact|kontakt|kontak|contacto|contacte|hubungi|lien-he|lienhe|"
    r"اتصل|impressum|reach|about-us|aboutus|nosotros|sobre"
)

# sitemap XML 的 <loc> 提取（Sitemap 协议；index 与 urlset 同构）
_LOC_RE = re.compile(r"<loc>\s*([^<]+?)\s*</loc>", re.I)
_CONTACT_PATH_RE = re.compile(CONTACT_WORDS, re.I)


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
        # Sitemap 兜底已触发的实体（防同实体多页重复触发）
        self._sitemap_tried: set[str] = set()
        # URL → developer_name 映射（play 渠道种子含 tab 分隔的开发者主体名，
        # 是 P0 第 1 强信号，spec 3.5；其他渠道为空）。归一后 URL 作 key。
        self._dev_names: dict[str, str] = {}
        urls: list[str] = []
        if seed_file:
            with open(seed_file, encoding="utf-8") as fh:
                for line in fh:
                    raw = line.strip()
                    if not raw or raw.startswith("#"):
                        continue
                    # 格式：URL<TAB>developerName（developerName 可空）
                    parts = raw.split("\t", 1)
                    url_part = parts[0]
                    dev_part = parts[1].strip() if len(parts) > 1 else ""
                    u = normalize_url(url_part if "://" in url_part
                                       else f"https://{url_part}")
                    if u:
                        urls.append(u)
                        if dev_part:
                            self._dev_names[u] = dev_part
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
        # HIGH #10 修复：预算按 entity_key 计（H4：a.com→www.a.com 重定向、www
        # 跳转、CDN 子域切换都共享同一实体；旧按 host 计会重置破 ≤5 页承诺）
        key = entity_key(host) if host else ""
        self._pages_per_host[key] += 1
        # 仅首页（每实体第 1 页）发现后续联系页——预算集中在最可能有号码的页面
        if self._pages_per_host[key] == 1:
            seen: set[str] = set()
            for link in self.contact_le.extract_links(response):
                target = normalize_url(link.url)
                if not target or target in seen:
                    continue
                seen.add(target)
                thost = (urlsplit(target).hostname or "").lower()
                tkey = entity_key(thost) if thost else ""
                # 严格同 entity_key + 每实体预算（含首页共 max_pages 页）
                if tkey == key and self._scheduled[key] < self.max_pages - 1:
                    self._scheduled[key] += 1
                    yield self._request(target, seed_host)
            # Sitemap 兜底（报告 3.3；2026-09-28 B3 补齐）：首页一个联系语义
            # 链接都没挖到时走 robots.txt 的 Sitemap 行——长尾 SMB/单页 SPA 站
            # 的联系方式往往只在 sitemap 里可见，否则每站预算只用掉 1 页
            if (self.max_pages > 1 and self._scheduled[key] == 0
                    and key not in self._sitemap_tried):
                # max_pages=1 时联系页预算为 0，兜底挖到也入不了队——不浪费请求
                self._sitemap_tried.add(key)
                yield self._discovery(
                    f"https://{host}/robots.txt", self.parse_robots, seed_host, key)
        yield {
            "url": response.url,
            "html": response.text,
            "channel": self.channel,
            "seed_host": seed_host,
        }

    def _request(self, url: str, seed_host: str | None) -> Request:
        # 不过 dont_filter：交给 Scrapy dupefilter 去重（JOBDIR 续跑时也持久化）
        meta = {"seed_host": seed_host}
        # 把种子附带的 developer_name 透传到 pipeline——meta 走 JOBDIR 不持久化，
        # 但每个 Request 创建时已锁定，失败 errback 路径下也能拿到
        if dev := self._dev_names.get(url):
            meta["developer_name"] = dev
        return Request(url, callback=self.parse, errback=self._fail, meta=meta)

    def _discovery(self, url: str, callback, seed_host: str | None, key: str,
                   depth: int = 0) -> Request:
        """Sitemap 兜底请求（robots.txt / sitemap.xml）——不进内容页预算。

        dont_filter=True：robots.txt 已被 RobotsTxtMiddleware 以同 URL 抓过
        （dupefilter 会拦掉我们的请求）；重复触发由 _sitemap_tried / depth 兜住。
        """
        meta = {"seed_host": seed_host, "_entity": key, "_sm_depth": depth}
        if dev := self._dev_names.get(url):
            meta["developer_name"] = dev
        return Request(url, callback=callback, errback=self._ignore, meta=meta,
                       dont_filter=True)

    def parse_robots(self, response):
        """robots.txt → Sitemap 行（最多 2 个）；未声明则试约定俗成的 /sitemap.xml。"""
        key = response.meta["_entity"]
        seed_host = response.meta.get("seed_host")
        smaps = [ln.split(":", 1)[1].strip()
                 for ln in response.text.splitlines()
                 if ln.strip().lower().startswith("sitemap:")][:2]
        for s in (smaps or [response.urljoin("/sitemap.xml")]):
            s = normalize_url(_html.unescape(s)) or s
            yield self._discovery(s, self.parse_sitemap, seed_host, key)

    def parse_sitemap(self, response):
        """sitemap（或 index）→ 联系语义 URL 入队（同 entity_key + 预算约束）；
        index（loc 全是 .xml 且没挖到联系页）下钻最多 2 个、一层。"""
        key = response.meta["_entity"]
        seed_host = response.meta.get("seed_host")
        depth = response.meta.get("_sm_depth", 0)
        locs = _LOC_RE.findall(_html.unescape(response.text))[:2000]
        scheduled = 0
        for loc in locs:
            target = normalize_url(loc.strip())
            if not target:
                continue
            thost = (urlsplit(target).hostname or "").lower()
            if not thost or entity_key(thost) != key:
                continue                      # 严格同实体：sitemap 可能列 CDN/子站域
            if not _CONTACT_PATH_RE.search(urlsplit(target).path):
                continue
            if self._scheduled[key] >= self.max_pages - 1:
                return                        # 预算满即止（≤5 页承诺不破）
            self._scheduled[key] += 1
            scheduled += 1
            yield self._request(target, seed_host)
        if (scheduled == 0 and depth == 0 and locs
                and all(l.lower().endswith((".xml", ".gz")) for l in locs[:5])):
            for loc in locs[:2]:
                yield self._discovery(_html.unescape(loc.strip()),
                                      self.parse_sitemap, seed_host, key, depth=1)

    def _ignore(self, failure):
        """兜底请求（robots/sitemap）失败无需留痕——首页已成功，error 留痕无意义。"""
        return

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
