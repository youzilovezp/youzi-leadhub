"""web_search 采集器：搜索引擎 → 企业种子发现（默认引擎零 key 零费用）。

PRD §6.2 P1 数据源「Google/Bing 搜索」+ §二「企业发现引擎（搜索）」：
按关键词搜公开网页（典型用法："whatsapp customer service" 跨境电商、
supplier "chat on whatsapp"），从结果标题+URL 提取企业官网种子，
source=web_search 入库（去重合并自动处理），后续靠 website_enrich 富化信号。

引擎（SEARCH_ENGINE，默认 duckduckgo——纯开源免费部署零配置零 key）：
- duckduckgo  html.duckduckgo.com HTML 端点，无 key 无费用（默认）
- searxng     自托管开源元搜索引擎（SEARXNG_URL，format=json）——生产推荐，
              聚合 Google/Bing/DDG 结果且不被单家限流
- google_cse  Google Custom Search JSON API（免费 100 次/天，超出付费——可选加速）
- bing        Bing Web Search API（付费——可选加速）

合规：默认引擎为公开网页端点/自托管实例；官方付费 API 仅作可选加速通道。
"""

from __future__ import annotations

import asyncio
import re
import urllib.parse
from typing import Any

import httpx

from app.collectors.base import Collector, LeadDraft, TaskContext, require_params, split_csv
from app.collectors.normalize import extract_domain
from app.core.config import settings
from app.core.exceptions import BusinessError

_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
# 搜索结果里不算「企业官网」的域（平台/文档/社媒）
_NON_SITE_DOMAINS = (
    "facebook.com", "instagram.com", "linkedin.com", "tiktok.com", "youtube.com",
    "twitter.com", "x.com", "amazon.", "shopee.", "lazada.", "alibaba.com",
    "aliexpress.com", "ebay.", "wikipedia.org", "reddit.com", "quora.com",
    "medium.com", "github.com", "google.com", "microsoft.com", "apple.com",
    "zapier.com", "hubspot.com", "salesforce.com", "zendesk.com",
    "whatsapp.com", "wa.me", "blogspot.", "wordpress.com", "wixsite.com",
    "duckduckgo.com", "searx", "bing.com", "baidu.com",
)

# DDG HTML 结果：标题链接与跳转参数
_DDGLINK_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S
)
_DDGTAG_RE = re.compile(r"<[^>]+>")
_DDGUDDG_RE = re.compile(r"[?&]uddg=([^&]+)")


# 文章/内容页启发词（标题或 URL 路径命中即弃——搜索「whatsapp 客服」类词
# 首页结果大量是内容平台文章，不是要找的企业官网）
_ARTICLE_TITLE_WORDS = (
    "指南", "测评", "手册", "必看", "攻略", "如何", "怎么办", "完整", "清单",
    "排行", "对比", "top 10", "top10", "best ", "how to", "guide", "review", "tutorial",
    "vs ", "tips", "checklist", "解密", "深度", "解析", "入门", "实战",
)
_ARTICLE_PATH_WORDS = (
    "/blog", "/article", "/articles", "/news", "/docs", "/doc/", "/archives",
    "/zh_cn/", "/zh-cn/", "/support/", "/help/", "/learn/", "/resources",
    "/post/", "/dy/article", "/p/", ".html", ".htm", ".php",
)


def _looks_like_article(title: str, url: str) -> bool:
    """标题/URL 是否内容页（文章/文档/博客）而非企业官网页面。"""
    t = title.lower()
    if any(w in t for w in _ARTICLE_TITLE_WORDS):
        return True
    path = urllib.parse.urlparse(url).path.lower()
    return any(w in path for w in _ARTICLE_PATH_WORDS)


def _is_company_site(url: str) -> bool:
    """搜索结果 URL 是否企业官网（滤平台/社媒/文档站）。"""
    domain = extract_domain(url) or ""
    if not domain:
        return False
    return not any(domain.endswith(d.rstrip(".")) or d in domain for d in _NON_SITE_DOMAINS)


def _ddg_unwrap(href: str) -> str:
    """DDG 跳转链接（//duckduckgo.com/l/?uddg=<urlencode>）→ 真实 URL。"""
    m = _DDGUDDG_RE.search(href)
    if m:
        return urllib.parse.unquote(m.group(1))
    if href.startswith("//"):
        return "https:" + href
    return href


def parse_ddg_html(html: str) -> list[dict[str, Any]]:
    """DDG HTML 结果页 → [{title, url}]（归一形态，喂 results_to_drafts）。"""
    items: list[dict[str, Any]] = []
    for m in _DDGLINK_RE.finditer(html or ""):
        url = _ddg_unwrap(m.group(1))
        title = _DDGTAG_RE.sub("", m.group(2)).strip()
        if url and title:
            items.append({"title": title, "url": url})
    return items


def results_to_drafts(items: list[dict[str, Any]], params_is_cn: Any = True) -> list[LeadDraft]:
    """搜索结果项（{title|name, url|link}）→ 企业官网种子 Draft。

    公司名取标题主体（剥 " - 后缀" / " | 后缀"），官网取 URL——
    后续由 website_enrich 富化补全联系方式与信号。
    """
    drafts: list[LeadDraft] = []
    seen_domains: set[str] = set()
    is_cn = str(params_is_cn).lower() != "false" if isinstance(params_is_cn, str) else bool(params_is_cn)
    for it in items:
        url = (it.get("url") or it.get("link") or "").strip()
        title = (it.get("name") or it.get("title") or "").strip()
        if not url or not title or not _is_company_site(url):
            continue
        if _looks_like_article(title, url):
            continue  # 内容页（指南/测评/博客）不是企业官网种子
        domain = extract_domain(url) or ""
        if not domain or domain in seen_domains:
            continue
        seen_domains.add(domain)
        # 种子入口归一为站点根：富化从首页开始，内页由链接发现逻辑自己找
        url = f"https://{domain}"
        for sep in (" - ", " | ", " – ", " — "):
            if sep in title:
                title = title.split(sep)[0].strip() or title
                break
        d = LeadDraft(source="web_search", name=title[:255], website=url)
        d.is_cn = is_cn
        drafts.append(d)
    return drafts


async def _search(
    clients: tuple[httpx.AsyncClient, httpx.AsyncClient], engine: str, kw: str, limit: int
) -> tuple[list[dict[str, Any]], str | None]:
    """按引擎执行搜索，返回 (归一结果, 错误信息)。

    双通道（与 meta_ads 同策略）：代理优先（国内网络访问 DDG/Google 域被墙），
    连接失败/软拦截切直连兜底（海外部署）。
    """
    if engine == "duckduckgo":
        err: str | None = None
        for client in clients:
            try:
                resp = await client.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": kw, "kl": "wt-wt"},
                    headers={"User-Agent": _UA, "Referer": "https://duckduckgo.com/"},
                )
            except httpx.HTTPError as exc:
                err = f"DDG 连接失败 {type(exc).__name__}"
                continue
            if resp.status_code != 200:
                err = f"DDG {resp.status_code}"
                continue
            return parse_ddg_html(resp.text)[:limit], None
        return [], err or "DDG 不可达"

    if engine == "searxng":
        if not settings.SEARXNG_URL:
            return [], "未配置 SEARXNG_URL"
        resp = await clients[0].get(
            f"{settings.SEARXNG_URL.rstrip('/')}/search",
            params={"q": kw, "format": "json"},
        )
        if resp.status_code != 200:
            return [], f"SearxNG {resp.status_code}"
        return resp.json().get("results", [])[:limit], None

    if engine == "google_cse":
        if not (settings.GOOGLE_CSE_KEY and settings.GOOGLE_CSE_CX):
            return [], "未配置 GOOGLE_CSE_KEY/CX"
        resp = await clients[0].get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": settings.GOOGLE_CSE_KEY,
                "cx": settings.GOOGLE_CSE_CX,
                "q": kw,
                "num": min(limit, 10),  # CSE 单页上限 10
            },
        )
        if resp.status_code != 200:
            return [], f"Google CSE {resp.status_code}"
        return resp.json().get("items", [])[:limit], None

    if engine == "bing":
        if not settings.BING_SEARCH_KEY:
            return [], "未配置 BING_SEARCH_KEY"
        resp = await clients[0].get(
            "https://api.bing.microsoft.com/v7.0/search",
            headers={"Ocp-Apim-Subscription-Key": settings.BING_SEARCH_KEY},
            params={"q": kw, "count": limit, "responseFilter": "Webpages"},
        )
        if resp.status_code != 200:
            return [], f"Bing {resp.status_code}"
        return (resp.json().get("webPages") or {}).get("value", [])[:limit], None

    return [], f"未知引擎：{engine}"


class WebSearchCollector(Collector):
    name = "web_search"
    title = "搜索引擎发现（DuckDuckGo / SearxNG）"
    param_schema = [
        {
            "key": "keywords",
            "label": "搜索关键词",
            "required": True,
            "type": "tags",
            "placeholder": '如 "whatsapp customer service" 跨境电商 / supplier "chat on whatsapp"',
            "default": "",
        },
        {
            "key": "max_results",
            "label": "每词最多取多少条结果",
            "required": False,
            "type": "number",
            "placeholder": "1-50",
            "default": "20",
        },
        {
            "key": "is_cn",
            "label": "标记为中国企业（出海 ICP）",
            "required": False,
            "type": "switch",
            "placeholder": "主爬中国企业（默认开）；关掉则采集海外本地企业",
            "default": "true",
        },
    ]

    def validate_params(self, params: dict[str, Any]) -> None:
        require_params(params, "keywords", collector=self.title)
        engine = settings.SEARCH_ENGINE
        if engine == "searxng" and not settings.SEARXNG_URL:
            raise BusinessError(code=40001, message="SEARCH_ENGINE=searxng 需在 .env 配 SEARXNG_URL")
        if engine == "google_cse" and not (settings.GOOGLE_CSE_KEY and settings.GOOGLE_CSE_CX):
            raise BusinessError(code=40001, message="SEARCH_ENGINE=google_cse 需配 GOOGLE_CSE_KEY/CX")
        if engine == "bing" and not settings.BING_SEARCH_KEY:
            raise BusinessError(code=40001, message="SEARCH_ENGINE=bing 需配 BING_SEARCH_KEY")

    async def run(self, ctx: TaskContext) -> None:
        keywords = split_csv(str(ctx.params.get("keywords")))
        try:
            max_results = max(1, min(int(ctx.params.get("max_results") or 20), 50))
        except ValueError:
            max_results = 20
        engine = settings.SEARCH_ENGINE
        ctx.set_total(len(keywords))
        ok_queries = 0

        async with (
            httpx.AsyncClient(timeout=_TIMEOUT) as via_proxy,
            httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as direct,
        ):
            clients = (via_proxy, direct)
            for kw in keywords:
                ctx.check_cancelled()
                await ctx.log("info", f"搜索（{engine}）：「{kw}」")
                items, err = await _search(clients, engine, kw, max_results)
                if err:
                    await ctx.log("error", f"「{kw}」搜索失败：{err}")
                else:
                    ok_queries += 1
                    drafts = results_to_drafts(items, ctx.params.get("is_cn", True))
                    for d in drafts:
                        await ctx.emit(d)
                    if items:
                        await ctx.log(
                            "info",
                            f"「{kw}」{len(items)} 条结果 → {len(drafts)} 个企业官网种子（滤平台/社媒域）",
                        )
                ctx.inc_progress(1)
                await asyncio.sleep(1.0)  # 引擎礼貌间隔

        if keywords and ok_queries == 0:
            raise BusinessError(
                code=50001,
                message=f"全部关键词搜索失败（引擎 {engine}，检查网络/凭据/实例可达性）",
            )
