"""job_posting 采集器：监控在招「WhatsApp 客服/私域运营」的公司。

主源 kalibrr（PH/ID，Next.js SSR，HTTP 直连可解析）——用 Crawlee
BeautifulSoupCrawler（重试/并发/请求管理白拿，jobstreet 启用时换
PlaywrightCrawler 只改一处）。

帖子 → 线索按公司映射：同一公司多个在招岗位合并为 1 条线索（dedupe_key
兜底 namecity，有官网则 domain），whatsapp_job 信号只计一次，岗位帖 URL
存 job_urls 数组。

页面结构已实测校准（2026-08）：
    GET /job-board?search={q}&page={n}
    → <script id="__NEXT_DATA__"> → props.pageProps.jobs[]
      字段：id / name / slug / companyName / company.code /
            companyInfo.url（可能是社媒而非官网）/ googleLocation.addressComponents.city
    页级：pageProps.geoCountry.country（如 PH）
"""

from __future__ import annotations

import json
from typing import Any

from app.collectors.base import Collector, LeadDraft, TaskContext, split_csv
from app.core.exceptions import BusinessError

_KALIBRR = "https://kalibrr.com/job-board"
_SOCIAL_HOSTS = ("facebook.com", "instagram.com", "linkedin.com", "t.me", "tiktok.com")


def _classify_url(url: str | None) -> tuple[str | None, str | None, dict[str, str]]:
    """companyInfo.url 可能是官网也可能是社媒主页——按 host 分类。

    返回 (website, country_unused, social)；country 占位恒 None，保持签名简单。
    """
    if not url:
        return None, None, {}
    host = url.lower()
    if any(s in host for s in _SOCIAL_HOSTS):
        platform = next(s.split(".")[0] for s in _SOCIAL_HOSTS if s in host)
        if platform == "t":
            platform = "telegram"
        return None, None, {platform: url}
    return url, None, {}


class JobPostingCollector(Collector):
    name = "job_posting"
    title = "招聘网站监控"
    param_schema = [
        {
            "key": "keywords",
            "label": "搜索关键词（逗号分隔）",
            "required": False,
            "placeholder": "whatsapp, customer service whatsapp",
            "default": "whatsapp",
        },
        {
            "key": "max_pages",
            "label": "每个关键词翻页数",
            "required": False,
            "placeholder": "3",
            "default": "3",
        },
    ]

    # jobstreet_* 有 Cloudflare 反爬，HTTP 直连 403。配置名保留，
    # 启用需 `pip install '.[collect]'`（Playwright）后把解析器换成 PlaywrightCrawler。
    SUPPORTED_SITES = ("kalibrr",)
    STUB_SITES = ("jobstreet_my", "jobstreet_sg", "jobstreet_id", "jobstreet_ph", "jobstreet_th")

    async def run(self, ctx: TaskContext) -> None:
        site = str(ctx.params.get("site") or "kalibrr").strip()
        if site in self.STUB_SITES:
            raise BusinessError(
                code=40001,
                message=f"{site} 暂未启用（Cloudflare 反爬）：pip install '.[collect]' 后支持",
            )
        if site not in self.SUPPORTED_SITES:
            raise BusinessError(code=40001, message=f"不支持的站点：{site}")

        keywords = split_csv(str(ctx.params.get("keywords") or "whatsapp")) or ["whatsapp"]
        try:
            max_pages = max(1, min(int(ctx.params.get("max_pages") or 3), 10))
        except ValueError:
            max_pages = 3

        from crawlee.crawlers import BeautifulSoupCrawler, BeautifulSoupCrawlingContext

        urls = [
            f"{_KALIBRR}?search={keyword.replace(' ', '+')}&page={page}"
            for keyword in keywords
            for page in range(1, max_pages + 1)
        ]
        ctx.set_total(len(urls))

        crawler = BeautifulSoupCrawler(
            max_requests_per_crawl=len(urls),
            max_request_retries=3,
            keep_alive=False,
        )

        @crawler.router.default_handler  # type: ignore[misc]
        async def handle(context: BeautifulSoupCrawlingContext) -> None:
            # 逐页 emit：进度实时 + 取消在页粒度生效（等整个 crawl 结束才检查就废了）
            ctx.check_cancelled()
            ns = context.soup.find("script", id="__NEXT_DATA__")
            if ns is None or not ns.string:
                await ctx.log("warn", f"页面无 __NEXT_DATA__：{context.request.url}")
                return
            data = json.loads(ns.string)
            page_props = (data.get("props") or {}).get("pageProps") or {}
            geo_country = (page_props.get("geoCountry") or {}).get("country")
            jobs = page_props.get("jobs") or []
            for job in jobs:
                draft = _job_to_draft(job, geo_country, str(context.request.url))
                if draft is not None:
                    await ctx.emit(draft)
            await ctx.log("info", f"{context.request.url} → {len(jobs)} 个岗位（{geo_country}）")
            ctx.inc_progress(1)

        await crawler.run(urls)


def _job_to_draft(
    job: dict[str, Any], geo_country: str | None, page_url: str
) -> LeadDraft | None:
    company = job.get("company") or {}
    company_name = job.get("companyName") or company.get("name")
    if not company_name:
        return None
    company_code = company.get("code")
    job_id, slug = job.get("id"), job.get("slug")
    job_url = (
        f"https://kalibrr.com/c/{company_code}/jobs/{job_id}/{slug}"
        if company_code and job_id and slug
        else page_url
    )
    address = job.get("googleLocation") or {}
    city = (address.get("addressComponents") or {}).get("city")
    website, _, social = _classify_url((job.get("companyInfo") or {}).get("url"))
    return LeadDraft(
        source="job_posting",
        name=company_name,
        country=geo_country,
        city=city,
        whatsapp_job=True,
        job_urls=[job_url],
        website=website,
        social=social,
    )
