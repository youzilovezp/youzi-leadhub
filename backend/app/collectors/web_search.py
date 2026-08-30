"""web_search 采集器：Google Custom Search / Bing Web Search → 企业种子发现。

PRD §6.2 P1 数据源「Google/Bing 搜索」+ §二「企业发现引擎（搜索）」：
按关键词搜公开网页（典型用法："whatsapp 客服 跨境电商 site:.com"、
"join our whatsapp group supplier"），从结果标题+URL 提取企业官网种子，
source=web_search 入库（去重合并自动处理），后续靠 website_enrich 富化信号。

凭据（二选一，.env）：
- GOOGLE_CSE_KEY + GOOGLE_CSE_CX：Google Custom Search JSON API（免费 100 次/天）
- BING_SEARCH_KEY：Bing Web Search API（Azure）——配了 Google 则 Google 优先

合规：走官方搜索 API（§6.1「官方 API/授权」层），不自爬搜索结果页。
"""

from __future__ import annotations

from typing import Any

import httpx

from app.collectors.base import Collector, LeadDraft, TaskContext, require_params, split_csv
from app.collectors.normalize import extract_domain
from app.core.config import settings
from app.core.exceptions import BusinessError

_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
# 搜索结果里不算「企业官网」的域（平台/文档/社媒）
_NON_SITE_DOMAINS = (
    "facebook.com", "instagram.com", "linkedin.com", "tiktok.com", "youtube.com",
    "twitter.com", "x.com", "amazon.", "shopee.", "lazada.", "alibaba.com",
    "aliexpress.com", "ebay.", "wikipedia.org", "reddit.com", "quora.com",
    "medium.com", "github.com", "google.com", "microsoft.com", "apple.com",
    "zapier.com", "hubspot.com", "salesforce.com", "zendesk.com",
    "whatsapp.com", "wa.me", "blogspot.", "wordpress.com", "wixsite.com",
)


def _is_company_site(url: str) -> bool:
    """搜索结果 URL 是否企业官网（滤平台/社媒/文档站）。"""
    domain = extract_domain(url) or ""
    if not domain:
        return False
    return not any(domain.endswith(d.rstrip(".")) or d in domain for d in _NON_SITE_DOMAINS)


def results_to_drafts(items: list[dict[str, Any]]) -> list[LeadDraft]:
    """搜索结果项（Google items[] 或 Bing webPages.value[] 归一后）→ 种子 Draft。

    归一输入：{title, url, snippet}。公司名取标题主体（截断处理），
    官网取 URL——后续由 website_enrich 富化补全联系方式与信号。
    """
    drafts: list[LeadDraft] = []
    seen_domains: set[str] = set()
    for it in items:
        url = (it.get("url") or it.get("link") or "").strip()
        title = (it.get("name") or it.get("title") or "").strip()
        if not url or not title or not _is_company_site(url):
            continue
        domain = extract_domain(url) or ""
        if not domain or domain in seen_domains:
            continue
        seen_domains.add(domain)
        # 标题常带 " - 公司名" / " | 公司名" 后缀：取分隔符前主体作公司名
        for sep in (" - ", " | ", " – ", " — "):
            if sep in title:
                title = title.split(sep)[0].strip() or title
                break
        drafts.append(
            LeadDraft(
                source="web_search",
                name=title[:255],
                website=url,
                country=None,
            )
        )
    return drafts


class WebSearchCollector(Collector):
    name = "web_search"
    title = "搜索引擎发现（Google CSE / Bing）"
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
    ]

    def validate_params(self, params: dict[str, Any]) -> None:
        require_params(params, "keywords", collector=self.title)
        if not (settings.GOOGLE_CSE_KEY and settings.GOOGLE_CSE_CX) and not settings.BING_SEARCH_KEY:
            raise BusinessError(
                code=40001,
                message=(
                    "未配置搜索 API 凭据：在 .env 配 GOOGLE_CSE_KEY + GOOGLE_CSE_CX"
                    "（Google Custom Search，免费 100 次/天）或 BING_SEARCH_KEY"
                ),
            )

    async def run(self, ctx: TaskContext) -> None:
        keywords = split_csv(str(ctx.params.get("keywords")))
        try:
            max_results = max(1, min(int(ctx.params.get("max_results") or 20), 50))
        except ValueError:
            max_results = 20
        ctx.set_total(len(keywords))
        ok_queries = 0

        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
            for kw in keywords:
                ctx.check_cancelled()
                await ctx.log("info", f"搜索：「{kw}」")
                items: list[dict[str, Any]] = []
                if settings.GOOGLE_CSE_KEY and settings.GOOGLE_CSE_CX:
                    resp = await client.get(
                        "https://www.googleapis.com/customsearch/v1",
                        params={
                            "key": settings.GOOGLE_CSE_KEY,
                            "cx": settings.GOOGLE_CSE_CX,
                            "q": kw,
                            "num": min(max_results, 10),  # Google CSE 单页上限 10
                        },
                    )
                    if resp.status_code != 200:
                        await ctx.log("error", f"Google CSE {resp.status_code}：{resp.text[:200]}")
                    else:
                        ok_queries += 1
                        items = resp.json().get("items", [])
                elif settings.BING_SEARCH_KEY:
                    resp = await client.get(
                        "https://api.bing.microsoft.com/v7.0/search",
                        headers={"Ocp-Apim-Subscription-Key": settings.BING_SEARCH_KEY},
                        params={"q": kw, "count": max_results, "responseFilter": "Webpages"},
                    )
                    if resp.status_code != 200:
                        await ctx.log("error", f"Bing {resp.status_code}：{resp.text[:200]}")
                    else:
                        ok_queries += 1
                        items = (resp.json().get("webPages") or {}).get("value", [])

                drafts = results_to_drafts(items[:max_results])
                for d in drafts:
                    await ctx.emit(d)
                if items:
                    await ctx.log(
                        "info",
                        f"「{kw}」{len(items)} 条结果 → {len(drafts)} 个企业官网种子（滤平台/社媒域）",
                    )
                ctx.inc_progress(1)

        if keywords and ok_queries == 0:
            raise BusinessError(
                code=50001,
                message="全部关键词搜索失败（检查搜索 API 凭据/配额）",
            )
