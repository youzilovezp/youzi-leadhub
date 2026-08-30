"""job_posting 采集器：监控中国招聘站「WhatsApp/海外客服/私域运营」在招的公司。

需求口径（2026-08-31 补充）：主爬中国企业（做出海业务），招聘源全部改为
中国招聘网站。

主源 jobui（职友集，职位聚合）：**Playwright 无头浏览器渲染**——中国招聘站
普遍 JS 验证挑战（jobui 的 valid.php 挑战在 HTTP 层走不通，实测 2026-08-31），
渲染模式稳定且免费开源。

依赖（免费开源）：
    pip install '.[collect]'   # crawlee[beautifulsoup,playwright]
    python -m playwright install chromium

站点架构（渲染模式可扩展）：
    SUPPORTED_SITES  当前 jobui（c-job-list 职位卡解析）
    STUB_SITES       其余大站待逐个适配渲染解析器（zhipin/liepin/51job/zhilian）

帖子 → 线索按公司映射：同一公司多个在招岗位合并为 1 条线索（dedupe_key
兜底 namecity，有官网则 domain）。招聘信号按岗位标题分类（§4.3 细分加分），
岗位帖 URL 存 job_urls、写信号级证据。

页面结构已实测校准（2026-08-31）：
    GET https://www.jobui.com/jobs?jobKw={关键词}&page={n}
    → <div class="c-job-list"> 职位卡（每页 ~23 个）
      字段：<h3>职位名</h3> / class="job-company-name" 公司名 /
            href="/job/{id}/" 职位链接 / class="job-city" 城市
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from app.collectors.base import Collector, LeadDraft, TaskContext, split_csv
from app.collectors.job_signals import classify_job_title
from app.core.exceptions import BusinessError

_JOBUl_BASE = "https://www.jobui.com"
_PAGE_GAP = 1.5  # 翻页礼貌间隔（秒）

# ---------- 职位卡解析（jobui SSR 页，实测结构） ----------

_CARD_SPLIT_RE = re.compile(r'class="c-job-list"')
_TITLE_RE = re.compile(r"<h3[^>]*>\s*(?:<a[^>]*>)?\s*([^<\n]{2,60})", re.S)
_COMPANY_RE = re.compile(r'job-company-name[^>]*>\s*(?:<a[^>]*>)?\s*([^<\n]{2,60})', re.S)
_JOB_LINK_RE = re.compile(r'href="(/job/\d+/)"')
_CITY_RE = re.compile(r'class="job-city"[^>]*>\s*([^<\n]{2,20})')


def parse_jobui_html(html: str, page_url: str) -> list[LeadDraft]:
    """jobui 搜索结果页 → LeadDraft 列表（每卡一岗；同公司多岗由 upsert 合并）。

    中国招聘站来源 → is_cn=True（需求补充：主爬做出海业务的中国企业）。
    """
    drafts: list[LeadDraft] = []
    for block in _CARD_SPLIT_RE.split(html or "")[1:]:
        title_m = _TITLE_RE.search(block)
        comp_m = _COMPANY_RE.search(block)
        if not title_m or not comp_m:
            continue
        title = title_m.group(1).strip()
        company = comp_m.group(1).strip()
        if not title or not company:
            continue
        job_link = _JOB_LINK_RE.search(block)
        job_url = urllib.parse.urljoin(_JOBUl_BASE, job_link.group(1)) if job_link else page_url
        city_m = _CITY_RE.search(block)
        signals = classify_job_title(title)
        drafts.append(
            LeadDraft(
                source="job_posting",
                name=company,
                country="CN",
                city=(city_m.group(1).strip() if city_m else None),
                is_cn=True,  # 中国招聘站 → 中国企业
                # whatsapp_job 只对 WhatsApp 语义岗位置位（=wa_ops）——语义与
                # 「在招WA岗位」导出列/评分 WhatsApp 维一致；海外客服/CRM 等
                # 其他招聘信号走 job_signals → 规模维，不冒充 WhatsApp 意向
                whatsapp_job="wa_ops" in signals,
                job_signals=signals,
                job_urls=[job_url],
            )
        )
    return drafts


async def _render_page(page, url: str, timeout_ms: int = 20000) -> str:
    """Playwright 渲染搜索页：等职位卡出现（JS 验证挑战自动通过）后取 HTML。"""
    import asyncio as _aio

    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        await page.wait_for_selector(".c-job-list", timeout=timeout_ms // 2)
    except Exception:  # noqa: BLE001  无职位卡（空结果/风控页）也取页面交给解析层判
        await _aio.sleep(1.5)
    return await page.content()


class JobPostingCollector(Collector):
    name = "job_posting"
    title = "中国招聘网站监控（jobui）"
    param_schema = [
        {
            "key": "keywords",
            "label": "搜索关键词",
            "required": False,
            "type": "tags",
            "placeholder": "岗位关键词回车，如 whatsapp / 海外客服 / 跨境电商运营",
            "default": "whatsapp,海外客服",
        },
        {
            "key": "max_pages",
            "label": "翻页数",
            "required": False,
            "type": "number",
            "placeholder": "1-5",
            "default": "2",
        },
    ]

    # 渲染模式下逐站适配（jobui 已适配；其余大站结构各异待加解析器）
    SUPPORTED_SITES = ("jobui",)
    STUB_SITES = ("zhipin", "liepin", "51job", "zhilian")

    def validate_params(self, params: dict[str, Any]) -> None:
        site = str(params.get("site") or "jobui").strip()
        if site in self.STUB_SITES:
            names = {"zhipin": "BOSS 直聘", "liepin": "猎聘", "51job": "前程无忧", "zhilian": "智联"}
            raise BusinessError(
                code=40001,
                message=(
                    f"{names.get(site, site)} 的渲染解析器尚未适配（Playwright 已就绪，"
                    f"补一个解析函数即可启用）；当前支持：jobui"
                ),
            )
        if site not in self.SUPPORTED_SITES:
            raise BusinessError(code=40001, message=f"不支持的站点：{site}（当前支持：jobui）")

    async def run(self, ctx: TaskContext) -> None:
        keywords = split_csv(str(ctx.params.get("keywords"))) or ["whatsapp"]
        try:
            max_pages = max(1, min(int(ctx.params.get("max_pages") or 2), 5))
        except ValueError:
            max_pages = 2
        ctx.set_total(len(keywords) * max_pages)

        import asyncio

        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BusinessError(
                code=40001,
                message="job_posting 需要 Playwright 渲染（中国招聘站有 JS 验证挑战）："
                "pip install '.[collect]' && python -m playwright install chromium",
            ) from exc

        ok_pages = 0
        async with async_playwright() as pw:
            # 国内站直连（代理Args留空）；无头 + zh-CN 贴近真实用户
            browser = await pw.chromium.launch(headless=True, proxy=None)
            # 不伪装 UA：jobui 风控对「伪装 Chrome UA × headless 指纹不一致」
            # 打分触发挑战（实测 2026-08-31），Playwright 默认 UA 与浏览器指纹
            # 自洽反而稳定通过
            page = await browser.new_page(
                locale="zh-CN", viewport={"width": 1440, "height": 900}
            )
            try:
                for kw in keywords:
                    for pg in range(1, max_pages + 1):
                        ctx.check_cancelled()
                        url = f"{_JOBUl_BASE}/jobs?jobKw={urllib.parse.quote(kw)}&page={pg}"
                        await ctx.log("info", f"搜索：「{kw}」第 {pg} 页")
                        try:
                            html = await _render_page(page, url)
                        except Exception as exc:  # noqa: BLE001  渲染失败（挑战/超时）
                            await ctx.log("error", f"「{kw}」第 {pg} 页渲染失败：{type(exc).__name__}: {str(exc)[:60]}")
                            continue
                        ok_pages += 1
                        drafts = parse_jobui_html(html, url)
                        for d in drafts:
                            lead_id, _created = await ctx.emit(d)
                            # 信号级证据（§4.1）：招聘信号带岗位帖 URL 作证据
                            if d.job_signals:
                                from app.crud.lead_signals import upsert_signal
                                from app.db.session import async_session

                                async with async_session() as session:
                                    for sig_key, meta in d.job_signals.items():
                                        await upsert_signal(
                                            session, lead_id, "job_signal",
                                            f"{sig_key}: {meta.get('label', sig_key)}（{d.name}）",
                                            source="job_posting",
                                            evidence_url=(d.job_urls or [None])[0],
                                            evidence_raw=str(meta), confidence=85,
                                        )
                                    await session.commit()
                        wa_n = sum(1 for d in drafts if d.whatsapp_job)
                        await ctx.log(
                            "info",
                            f"「{kw}」第 {pg} 页 → {len(drafts)} 个在招岗位"
                            + (f"，信号相关 {wa_n} 个" if drafts else ""),
                        )
                        ctx.inc_progress(1)
                        await asyncio.sleep(_PAGE_GAP)
            finally:
                await browser.close()

        if ok_pages == 0:
            raise BusinessError(
                code=50001,
                message="全部页面抓取失败（jobui 限流或网络异常）——稍后重跑或降低翻页数",
            )
