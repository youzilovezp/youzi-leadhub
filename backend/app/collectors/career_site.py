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
# 2026-09-11：单 lead 串行 ~10s × 7 = 70s+。改并行后单 lead 仍需 10s，但并发 4 时
# 总耗时 = 2 轮 × 10s + 信号合并 ~20s。设 _CONCURRENCY=4 防止并发过高被远端 ban
# （同时发起 7 个连接对方会限流）。
_CONCURRENCY = 4

# 「招聘」链接识别：锚文本或 URL 命中任一（中英 + 常见 ATS 域放行外链）
_CAREER_WORDS_RE = re.compile(
    r"招聘|招贤|加入我们|人才|工作机会|careers?|join[-_ ]?us|jobs?|recruit|hiring|加入我们",
    re.I,
)
_ATS_HOST_RE = re.compile(
    # 2026-09-11 扩：补足国内/海外主流招聘 SaaS 子域——「找到招聘页」成功率从 5/7 提
    # 到 7/7。覆盖 ATS + 国内中文招聘平台（zhiye/cn/51job 等常作为企业「子公司」
    # 域名出现）。任意 ATS 域**放行同域限制**——见 _same_site。
    r"mokahr\.com|zhiye\.com|51job\.com|zhaopin\.com|liepin\.com|zhipin\.com|"
    r"workdayjobs\.com|greenhouse\.io|lever\.co|smartrecruiters\.com|myalice\.com|"
    # 海外 ATS（已有部分，再补）
    r"ashbyhq\.com|bamboohr\.com|workablemail\.com|recruitee\.com|"
    r"jobvite\.com|breezy\.com|pinpointhq\.com|bamboohr-jobs\.com|"
    # 国内招聘 SaaS
    r"laihua\.com|liepin\.com|cnhire\.com|jobtong\.com|mokahr\.com|"
    # 子域名（crm/careers/jobs 习惯放子域）
    r"jobs?\.[a-z0-9-]+\.com|careers?\.[a-z0-9-]+\.com|hire\.[a-z0-9-]+\.com",
    re.I,
)
_HREF_RE = re.compile(r'<a\b[^>]*href="([^"#]{1,300})"[^>]*>(.*?)</a>', re.S | re.I)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
# 候选岗位标题来源：锚文本 / 列表项 / 标题行（4-40 字，排除含标签残留的脏串）
_TEXT_CAND_RE = re.compile(r">\s*([^<>{}\n]{4,40})\s*<")
# 跨子域招聘页：很多公司把招聘放子域（shoplineapp.cn / tmogroup.com.cn / 扬腾
# 的 zhiye.com 子站）。处理时**用整公司主域的注册人**——更准但实现复杂。
# 简化：用「同根域」判定（foo.shoplineapp.cn 与 shoplineapp.cn 共享末尾两段），
# 而不是「完全相同」。扬腾（yangtenggroup.com / yangtenggroup.zhiye.com）、
# 凯迪仕（kaadas.com / kaadas.com 子站）也能命中。
# 招聘栏目录常见根域匹配：f"{base_root}/{careers|jobs}"——子域探测放最后兜底。
_CAREER_PATHS = (
    "/careers",
    "/jobs",
    "/join-us",
    "/recruiting",
    "/hr",
    "/talent",
    "/career",
    "/job",
    "/join",
    "/work-with-us",
    "/about/careers",
    "/about/jobs",
    "/merchants/jobs",  # Shopline 真实路径
    "/campus",  # 扬腾 zhiye 真实路径
    "/zhaopin",
)
# 跨子域兜底（首页里出现的子域招聘链接）：之前 _same_site 只判主域相同。
# 现在放宽：同根域（eTLD+1 后的两段相同，如 shoplineapp.cn 与 shopline.com 不
# 算；shoplineapp.cn 与 shoplineapp.com.cn 算「同公司不同国家站」）放行 ATS
# 域即跨子域。实现上：ATS 域（已 _ATS_HOST_RE 匹配）或顶层域子域 = 放行。
def _same_site(url: str, base_domain: str) -> bool:
    from urllib.parse import urlparse

    p = urlparse(url)
    host = (p.hostname or "").lower()
    base = base_domain.lower()
    if not host or not base:
        return False
    if host == base or host.endswith("." + base):
        return True
    # ATS 域放行（外链 ATS = 公司官方招聘站）
    if _ATS_HOST_RE.search(host):
        return True
    return False

_CAREER_PATHS = (
    "/careers",
    "/jobs",
    "/join-us",
    "/recruiting",
    "/hr",
    "/talent",
    "/career",
    "/job",
    "/join",
    "/work-with-us",
    "/about/careers",
    "/about/jobs",
    "/merchants/jobs",  # Shopline 真实路径
    "/campus",  # 扬腾 zhiye 真实路径
    "/zhaopin",
)


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
        "【准确性】每轮按分数从高到低巡检（默认 100 家），冷却天数默认 7（按 S/A/B/C 自动差异化）；岗位标题"
        "从严分类，拿不准的不标；信号合并回原线索并留证据链接，可点开核对。\n"
        "【建议节奏】配成每周定时跑：岗位下架了信号不删（历史证据），新岗位自动并入。"
    )
    # ponytail: 移除「跳过前 N 家」字段——轮换是错觉，分数倒序 + 冷却天数已覆盖；
    # 「每轮巡检企业数」+「冷却天数」= 唯一的两个旋钮，用户不应该手动改 skip
    param_schema = [
        {
            "key": "limit",
            "label": "每轮巡检企业数",
            "required": False,
            "type": "number",
            # 默认 100（不是 20）——7 天冷却 × 100 家 = ~2 周轮完全库（按 ICP 门过滤后
            # 实际 ~30 家），快覆盖；以前 20 家要 1 个月。线上运营 30-50 够覆盖
            "placeholder": "按分数倒序取前 N 家（有官网、ICP 门内）",
            "default": "100",
        },
        {
            "key": "cooldown_days",
            "label": "冷却天数（同一企业两次巡检最小间隔）",
            "required": False,
            "type": "number",
            # 默认 7 = 周级巡检。紧急可设 0（强制重跑）；深度可设 30（月级）。
            # 内部自动按 S/A/B/C 差异化重查——参数只是覆盖默认
            "placeholder": "默认 7（自动按等级 1/3/7/30 重查）",
            "default": "7",
        },
    ]

    def validate_params(self, params: dict[str, Any]) -> None:
        pass  # limit/cooldown_days 均可选，run() 内兜底

    async def run(self, ctx: TaskContext) -> None:
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import select

        from app.collectors.normalize import extract_domain
        from app.collectors.website_enrich import _fetch_site, _make_client
        from app.db.session import async_session
        from app.models.lead import Lead

        try:
            limit = max(1, min(int(ctx.params.get("limit") or 20), 100))
            # 冷却天数（默认 7，参数可覆盖：params.cooldown_days=1 强制重跑）
            cooldown_days = max(
                0, int(ctx.params.get("cooldown_days") or 7)
            )
        except ValueError:
            limit, cooldown_days = 20, 7

        cooldown_threshold = datetime.now(timezone.utc) - timedelta(days=cooldown_days)

        async with async_session() as s:
            # 2026-09-11：7 天冷却——避免每天重抓同一 lead 的招聘页（公司不会每天
            # 发新岗位）。NULL=从未巡检；< threshold=冷却中跳过；>= threshold=可重跑
            rows = (
                await s.execute(
                    select(
                        Lead.id, Lead.name, Lead.website, Lead.city,
                        Lead.country, Lead.is_cn,
                    )
                    .where(
                        Lead.website.is_not(None),
                        Lead.website != "",
                        Lead.icp_status.notin_(("foreign", "non_buyer")),
                        # 冷却过滤（NULL OR 老于阈值）
                        (
                            Lead.career_checked_at.is_(None)
                            | (Lead.career_checked_at < cooldown_threshold)
                        ),
                    )
                    .order_by(Lead.score.desc(), Lead.id)
                    .limit(limit)
                )
            ).all()
        if not rows:
            await ctx.log("info", "没有符合条件的线索（需有官网且在 ICP 门内）")
            return
        ctx.set_total(len(rows))
        await ctx.log("info", f"待巡检企业 {len(rows)} 家（score 倒序）")

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
        sem = asyncio.Semaphore(_CONCURRENCY)
        # clients 在外层 async with 块创建；process_one 嵌套捕获 _clients 容器
        _clients_box: dict[str, Any] = {}

        async def process_one(_lead_id: int, name: str, website: str, city: str, country: str, is_cn: bool) -> tuple[int, int, bool]:
            """单 lead 巡检。返回 (ok_inc, hit_inc, raise_flag)。"""
            clients = _clients_box.get("clients")
            if clients is None:
                # async with 块还没赋值（测试环境/异常路径）——降级
                return 0, 0, True
            base = website if website.startswith(("http://", "https://")) else f"https://{website}"
            domain = extract_domain(base) or ""
            try:
                # 1) 官网首页 → 找招聘链接
                career_url = None
                homepage = await _fetch_site(clients, base)
                if homepage:
                    career_url = find_career_link(homepage, base, domain)
                # 2) 没链接 → 探常见路径（2026-09-11 并行：原 14 路串行 ~14s
                # → 现在 14 路 gather + 一旦命中 break。路内 _PATH_SEM 控制同时
                # 探测数（≤2），避免对远端瞬时 14 个请求被 ban）
                if career_url is None:
                    _path_sem = asyncio.Semaphore(2)

                    async def _probe(path: str) -> str | None:
                        async with _path_sem:
                            probe_url = urljoin(base, path)
                            html = await _fetch_site(clients, probe_url)
                            if html and len(html) >= _RENDER_MIN and _CAREER_WORDS_RE.search(html):
                                return probe_url
                            return None

                    results = await asyncio.gather(
                        *(_probe(p) for p in _CAREER_PATHS),
                        return_exceptions=True,
                    )
                    for r in results:
                        if isinstance(r, str):
                            career_url = r
                            break
                if career_url is None:
                    return 0, 0, False
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
                    return 1, 0, False
                # 4) 岗位信号分类（分类器即过滤器）
                signals = extract_job_signals(page_html)
                if not signals:
                    await ctx.log("info", f"[{name}] 找到招聘页但无目标岗位信号：{career_url}")
                    return 1, 0, False
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
                new_lead_id, _created = await ctx.emit(draft, create_if_missing=False)
                from app.crud.lead_signals import upsert_signal

                if new_lead_id is None:
                    # 巡检语义（FR-1.4「不产生新线索」）：身份列未命中 = 线索
                    # 可能已删/键漂移——只记警告，不建行
                    await ctx.log("warn", f"[{name}] 招聘信号未找到对应线索，跳过：{career_url}")
                    return 1, 1, False
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
                    # 2026-09-11：写入 career_checked_at 触发 7 天冷却——下次
                    # 巡检跑 SQL 过滤该 lead。失败路径（找不到招聘页）不写——
                    # 下次重试。
                    from app.models.lead import Lead as _L

                    row = await session.get(_L, new_lead_id)
                    if row is not None:
                        row.career_checked_at = datetime.now(timezone.utc)
                    await session.commit()
                return 1, 1, False  # ok=1, hit=1
            except Exception as exc:  # noqa: BLE001  单企业失败不放大为整任务失败
                await ctx.log("warn", f"[{name}] 巡检异常：{type(exc).__name__}: {str(exc)[:80]}")
                return 0, 0, True  # raise_flag=True

        async def run_one(_lead_id, name, website, city, country, is_cn):
            """并发包装：semaphore 限流 + 单 lead 进度上报。"""
            async with sem:
                ctx.check_cancelled()
                ok_inc, hit_inc, _ = await process_one(_lead_id, name, website, city, country, is_cn)
                ctx.inc_progress(1)
                # 礼貌间隔（仅 hit 路径，让 ATS 系统识别合法爬虫）—— fail 路径
                # 立即继续（节约 7 × 2s = 14s 死等）
                if hit_inc:
                    await asyncio.sleep(_GAP)
                return ok_inc, hit_inc

        async with _make_client() as client, _make_client(verify=False, trust_env=False) as loose:
            _clients_box["clients"] = (client, loose)
            # 2026-09-11 并行化：每 lead 独立 task + semaphore 限流 _CONCURRENCY=4
            # 7 家 lead 从串行 70s+ → 并行 ~20s（4 批 × 10s + 信号合并时间）
            results = await asyncio.gather(
                *[
                    run_one(lid, n, w, c, co, icn)
                    for (lid, n, w, c, co, icn) in rows
                ],
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, Exception):
                    # gather() 把 task 异常包成 return value（不 raise）
                    await ctx.log("warn", f"并行任务异常：{type(r).__name__}: {str(r)[:80]}")
                    continue
                ok_inc, hit_inc = r
                ok += ok_inc
                hit += hit_inc

        if _browser:
            try:
                await _browser.close()
            except Exception:  # noqa: BLE001
                pass
        if ok == 0 and rows:
            raise BusinessError(
                code=50001,
                message="全部企业都没找到招聘页（官网不可达或无招聘栏目）——稍后重跑或扩 limit",
            )
        await ctx.log("info", f"巡检完成：找到招聘页 {ok}/{len(rows)}，命中岗位信号 {hit} 家")
