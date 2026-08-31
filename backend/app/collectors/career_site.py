"""career_site 采集器：巡检库里已有线索的企业官网「招聘页」，提取在招岗位信号。

定位（2026-08-31 需求）：除招聘平台外，企业自己的招聘官网/ATS 页（Moka、
北森、Workday、jobs.51job.com 子域等）也是「在招什么岗」的一手来源——
且这里巡检的是**库里已有线索**，产出不建新行，而是经 upsert 三身份列反查
合并回原线索（落库前必判重），岗位信号进 job_signals、重评分。

链路（每企业）：
    官网首页（复用 website_enrich 双通道抓取）→ 找「招聘」链接
    （文字/URL 命中：招聘|加入我们|人才|careers|join us|jobs|recruit，
    含外链 ATS 域）→ 无链接再探 /careers /jobs /join-us 常见路径
    → 招聘页 httpx 抓取（薄壳 SPA 用 Playwright 渲染兜底）
    → 页面文本/锚点逐条过 classify_job_title（分类器即过滤器，从严）
    → 有信号 → LeadDraft(website=官网) → upsert 合并回原线索 + 信号证据

不进自动富化接力（_CHAIN_ENRICH_AFTER）：不是发现源，线索已在库。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urljoin

from app.collectors.base import Collector, LeadDraft, TaskContext
from app.collectors.job_signals import classify_job_title
from app.core.exceptions import BusinessError

_GAP = 2.0          # 企业间礼貌间隔（秒）
_RENDER_MIN = 2000  # 低于此长度视为 SPA 壳，转浏览器渲染
_MAX_TITLES = 400   # 单页参与分类的文本条数上限（防超大页拖慢）

# 「招聘」链接识别：锚文本或 URL 命中任一（中英 + 常见 ATS 域放行外链）
_CAREER_WORDS_RE = re.compile(
    r"招聘|招贤|加入我们|人才|工作机会|careers?|join[-_ ]?us|jobs?|recruit|hiring|加入我们",
    re.I,
)
_ATS_HOST_RE = re.compile(
    r"mokahr\.com|zhiye\.com|51job\.com|zhaopin\.com|liepin\.com|zhipin\.com|"
    r"workdayjobs\.com|greenhouse\.io|lever\.co|smartrecruiters\.com|myalice\.com",
    re.I,
)
_HREF_RE = re.compile(r'<a\b[^>]*href="([^"#]{1,300})"[^>]*>(.*?)</a>', re.S | re.I)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
# 候选岗位标题来源：锚文本 / 列表项 / 标题行（4-40 字，排除含标签残留的脏串）
_TEXT_CAND_RE = re.compile(r">\s*([^<>{}\n]{4,40})\s*<")

_CAREER_PATHS = ("/careers", "/jobs", "/join-us", "/recruiting", "/hr")


def _same_site(url: str, base_domain: str) -> bool:
    return base_domain in url or bool(_ATS_HOST_RE.search(url))


def find_career_link(homepage_html: str, page_url: str, base_domain: str) -> str | None:
    """首页 HTML → 招聘页 URL（锚文本或 URL 命中招聘词；同域或 ATS 域）。"""
    for href, inner in _HREF_RE.findall(homepage_html or ""):
        text = _TAG_STRIP_RE.sub("", inner).strip()
        if not href.startswith(("http://", "https://", "/")):
            continue
        if _CAREER_WORDS_RE.search(href) or _CAREER_WORDS_RE.search(text):
            url = urljoin(page_url, href)
            if _same_site(url, base_domain):
                return url
    return None


def extract_job_signals(page_html: str) -> dict[str, dict]:
    """招聘页 → 岗位信号（分类器即过滤器：不命中词表的一律不算岗位）。"""
    signals: dict[str, dict] = {}
    for text in _TEXT_CAND_RE.findall(page_html or "")[:_MAX_TITLES]:
        hit = classify_job_title(text.strip())
        for k, v in hit.items():
            signals.setdefault(k, v)
    return signals


class CareerSiteCollector(Collector):
    name = "career_site"
    title = "企业招聘官网巡检（官网/ATS 招聘页挖岗位信号）"
    logic_note = (
        "【抓什么】巡检库里已有线索的企业官网，找到它们自己的招聘页（官网栏目或 "
        "Moka/北森/前程无忧子站等招聘系统），从在招岗位判断业务：在招「海外客服」"
        "= 有海外客户，在招「WhatsApp 运营」= 在用 WhatsApp 做私域。\n"
        "【和招聘平台监控的差别】平台搜的是全市场岗位；这里看的是具体企业的一手"
        "招聘页——给已入库线索补岗位证据，不产生新线索。\n"
        "【准确性】每轮按分数从高到低巡检（默认 20 家，可加 skip 轮换）；岗位标题"
        "从严分类，拿不准的不标；信号合并回原线索并留证据链接，可点开核对。\n"
        "【建议节奏】配成每周定时跑：岗位下架了信号不删（历史证据），新岗位自动并入。"
    )
    param_schema = [
        {
            "key": "limit",
            "label": "每轮巡检企业数",
            "required": False,
            "type": "number",
            "placeholder": "按分数倒序取前 N 家（有官网、非 foreign）",
            "default": "20",
        },
        {
            "key": "skip",
            "label": "跳过前 N 家",
            "required": False,
            "type": "number",
            "placeholder": "轮换巡检用：第 2 轮填 20、第 3 轮填 40 …",
            "default": "0",
        },
    ]

    def validate_params(self, params: dict[str, Any]) -> None:
        pass  # limit/skip 均可选，run() 内兜底

    async def run(self, ctx: TaskContext) -> None:
        from sqlalchemy import select

        from app.collectors.normalize import extract_domain
        from app.collectors.website_enrich import _fetch_site, _make_client
        from app.db.session import async_session
        from app.models.lead import Lead

        try:
            limit = max(1, min(int(ctx.params.get("limit") or 20), 100))
            skip = max(0, int(ctx.params.get("skip") or 0))
        except ValueError:
            limit, skip = 20, 0

        async with async_session() as s:
            rows = (
                await s.execute(
                    select(Lead.id, Lead.name, Lead.website, Lead.city, Lead.country, Lead.is_cn)
                    .where(Lead.website.is_not(None), Lead.website != "", Lead.icp_status != "foreign")
                    .order_by(Lead.score.desc(), Lead.id)
                    .offset(skip)
                    .limit(limit)
                )
            ).all()
        if not rows:
            await ctx.log("info", "没有符合条件的线索（需有官网且非 foreign）")
            return
        ctx.set_total(len(rows))
        await ctx.log("info", f"待巡检企业 {len(rows)} 家（score 倒序，跳过前 {skip}）")

        # 渲染兜底懒启动（SPA 招聘站如 Moka）；None=还没启动，False=不可用哨兵
        _browser: Any = None

        async def get_browser():
            nonlocal _browser
            if _browser is not None:
                return _browser or None
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                await ctx.log("info", "浏览器渲染兜底不可用（SPA 招聘页可能抓不全）：pip install '.[collect]'")
                _browser = False
                return None
            pw = await async_playwright().start()
            _browser = await pw.chromium.launch(headless=True, proxy=None)
            return _browser

        ok = hit = 0
        async with _make_client() as client, _make_client(verify=False, trust_env=False) as loose:
            clients = (client, loose)
            for lead_id, name, website, city, country, is_cn in rows:
                ctx.check_cancelled()
                base = website if website.startswith(("http://", "https://")) else f"https://{website}"
                domain = extract_domain(base) or ""
                try:
                    # 1) 官网首页 → 找招聘链接
                    career_url = None
                    homepage = await _fetch_site(clients, base)
                    if homepage:
                        career_url = find_career_link(homepage, base, domain)
                    # 2) 没链接 → 探常见路径
                    if career_url is None:
                        for path in _CAREER_PATHS:
                            probe_url = urljoin(base, path)
                            html = await _fetch_site(clients, probe_url)
                            if html and len(html) >= _RENDER_MIN and _CAREER_WORDS_RE.search(html):
                                career_url = probe_url
                                break
                    if career_url is None:
                        ctx.inc_progress(1)
                        await asyncio.sleep(_GAP)
                        continue
                    ok += 1
                    # 3) 招聘页（薄壳 → 渲染兜底）
                    page_html = await _fetch_site(clients, career_url)
                    if page_html is None or len(page_html) < _RENDER_MIN:
                        b = await get_browser()
                        if b:
                            pg = await b.new_page(locale="zh-CN")
                            try:
                                await pg.goto(career_url, wait_until="domcontentloaded", timeout=25000)
                                await pg.wait_for_timeout(2500)
                                page_html = await pg.content()
                            finally:
                                await pg.close()
                    if not page_html:
                        await ctx.log("warn", f"[{name}] 招聘页抓取失败：{career_url}")
                        ctx.inc_progress(1)
                        await asyncio.sleep(_GAP)
                        continue
                    # 4) 岗位信号分类（分类器即过滤器）
                    signals = extract_job_signals(page_html)
                    if not signals:
                        await ctx.log("info", f"[{name}] 找到招聘页但无目标岗位信号：{career_url}")
                        ctx.inc_progress(1)
                        await asyncio.sleep(_GAP)
                        continue
                    hit += 1
                    sig_names = "、".join(m.get("label", k) for k, m in signals.items())
                    await ctx.log("info", f"[{name}] ✅ 招聘页命中岗位信号（{sig_names}）：{career_url}")
                    # 5) 合并回原线索（upsert 三身份列反查 → 同 domain 命中同一条）
                    draft = LeadDraft(
                        source="career_site",
                        name=name,
                        website=website,
                        country=country,
                        city=city,
                        is_cn=is_cn,
                        whatsapp_job="wa_ops" in signals,
                        job_signals=signals,
                        job_urls=[career_url],
                    )
                    new_lead_id, _created = await ctx.emit(draft)
                    from app.crud.lead_signals import upsert_signal

                    async with async_session() as session:
                        for sig_key, meta in signals.items():
                            await upsert_signal(
                                session, new_lead_id, "job_signal",
                                f"{sig_key}: {meta.get('label', sig_key)}（{name} 招聘页）",
                                source="career_site",
                                evidence_url=career_url,
                                evidence_raw=f"企业招聘页在招：{sig_names}",
                                confidence=90,  # 企业一手招聘页，比平台列表更可信
                            )
                        await session.commit()
                except Exception as exc:  # noqa: BLE001  单企业失败不放大为整任务失败
                    await ctx.log("warn", f"[{name}] 巡检异常：{type(exc).__name__}: {str(exc)[:80]}")
                ctx.inc_progress(1)
                await asyncio.sleep(_GAP)

        if _browser:
            try:
                await _browser.close()
            except Exception:  # noqa: BLE001
                pass
        if ok == 0 and rows:
            raise BusinessError(
                code=50001,
                message="全部企业都没找到招聘页（官网不可达或无招聘栏目）——换一批（调大 skip）或稍后重跑",
            )
        await ctx.log("info", f"巡检完成：找到招聘页 {ok}/{len(rows)}，命中岗位信号 {hit} 家")
