"""job_posting 采集器：监控中国招聘站「WhatsApp/海外客服/私域运营」在招的公司。

需求口径（2026-08-31 补充）：主爬中国企业（做出海业务），招聘源全部改为
中国招聘网站。

多站架构（Playwright 无头渲染，2026-08-31 实测反爬地图）：
    jobui（职友集）   ✅ 渲染过 JS 挑战，c-job-list 职位卡
    liepin（猎聘）    ✅ 渲染出完整职位卡（data-nick 稳定锚点 + title 属性）
    51job（前程无忧） ✅ WAF 只挡 HTTP 直连，渲染出职位卡（sensorsdata 结构化字段）
    zhipin（BOSS直聘）❌ 无头渲染被 geetest 安全验证页拦截（需有头/打码，暂缓）
    zhilian（智联）   ❌ Security Verification 盾，无头过不了（暂缓）

依赖（免费开源）：
    pip install '.[collect]'   # crawlee[beautifulsoup,playwright]
    python -m playwright install chromium

帖子 → 线索按公司映射：同一公司多个在招岗位合并为 1 条线索（upsert 三身份列
反查去重，落库前必判重）。招聘信号按岗位标题分类（§4.3 细分加分），
岗位帖 URL 存 job_urls、写信号级证据。
"""

from __future__ import annotations

import html as _html
import json as _json
import re
import urllib.parse
from typing import Any

from app.collectors.base import Collector, LeadDraft, TaskContext, split_csv
from app.collectors.job_signals import classify_job_title
from app.core.exceptions import BusinessError

_JOBUl_BASE = "https://www.jobui.com"
_PAGE_GAP = 5.0  # 翻页礼貌间隔（秒）——1.5s 连续渲染 ~4 页即被 jobui 重置连接（2026-08-31 实测）

# ---------- 职位卡解析（jobui SSR 页，实测结构） ----------

_CARD_SPLIT_RE = re.compile(r'class="c-job-list"')
_TITLE_RE = re.compile(r"<h3[^>]*>\s*(?:<a[^>]*>)?\s*([^<\n]{2,60})", re.S)
_COMPANY_RE = re.compile(r'job-company-name[^>]*>\s*(?:<a[^>]*>)?\s*([^<\n]{2,60})', re.S)
_JOB_LINK_RE = re.compile(r'href="(/job/\d+/)"')
_CITY_RE = re.compile(r'class="job-city"[^>]*>\s*([^<\n]{2,20})')


def _mk_draft(title: str, company: str, city: str | None, job_url: str) -> LeadDraft:
    """岗位卡 → LeadDraft（中国招聘站来源 → is_cn=True；wa_ops 才置 whatsapp_job）。"""
    signals = classify_job_title(title)
    return LeadDraft(
        source="job_posting",
        name=company,
        country="CN",
        city=city,
        is_cn=True,
        # whatsapp_job 只对 WhatsApp 语义岗位置位（=wa_ops）——语义与
        # 「在招WA岗位」导出列/评分 WhatsApp 维一致；海外客服/CRM 等
        # 其他招聘信号走 job_signals → 规模维，不冒充 WhatsApp 意向
        whatsapp_job="wa_ops" in signals,
        job_signals=signals,
        job_urls=[job_url],
    )


def parse_jobui_html(html: str, page_url: str) -> list[LeadDraft]:
    """jobui 搜索结果页 → LeadDraft 列表（每卡一岗；同公司多岗由 upsert 合并）。"""
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
        drafts.append(
            _mk_draft(title, company, city_m.group(1).strip() if city_m else None, job_url)
        )
    return drafts


# ---------- 猎聘（渲染后结构，2026-08-31 实测校准） ----------
# 卡片锚点 job-card-pc-container（哈希 CSS 类前缀会变，语义类名稳定）；
# 职位名 = 卡内第一个 title="" 属性（ellipsis div）；公司名在
# job-detail-company-info 锚点后的 span.ellipsis-1（匿名代发「某…公司」跳过）；
# 职位链接 https://www.liepin.com/a/{id}.shtml（去 query）。

_LIEPIN_TITLE_RE = re.compile(r'title="([^"%][^"]{1,60})"')
_LIEPIN_COMP_RE = re.compile(
    r'job-detail-company-info.{0,500}?ellipsis-1">([^<]{2,40})</span>', re.S
)
_LIEPIN_COMP_LINK_RE = re.compile(r'company/gs\d+/[^>]*?title="([^"]{2,40})"')
_LIEPIN_JOB_URL_RE = re.compile(r'href="(https://www\.liepin\.com/(?:a|job)/\d+\.shtml)')  # /a/ 猎头代发、/job/ 企业直发


def parse_liepin_html(html: str, page_url: str) -> list[LeadDraft]:
    drafts: list[LeadDraft] = []
    for block in (html or "").split("job-card-pc-container")[1:]:
        title_m = _LIEPIN_TITLE_RE.search(block)
        comp_m = _LIEPIN_COMP_RE.search(block) or _LIEPIN_COMP_LINK_RE.search(block)
        if not title_m or not comp_m:
            continue
        title = title_m.group(1).strip()
        company = comp_m.group(1).strip()
        # 猎头代发匿名公司（「某国内大型食品公司」）无法建线索/去重，跳过
        if not title or not company or company.startswith("某"):
            continue
        link_m = _LIEPIN_JOB_URL_RE.search(block)
        drafts.append(_mk_draft(title, company, None, link_m.group(1) if link_m else page_url))
    return drafts


# ---------- 前程无忧 51job（渲染后结构，2026-08-31 实测校准） ----------
# 列表是 Vue SPA（无真实详情 href，点击走路由）；每卡 sensorsdata 属性带
# 结构化 JSON：jobTitle / jobArea（如 上海·虹口区）/ jobId——比 DOM 稳。
# 公司名 class="cname text-cut"；城市取 jobArea 的 · 前段。

_51JOB_SENSORS_RE = re.compile(r'sensorsdata="([^"]+)"')
_51JOB_COMPANY_RE = re.compile(r'class="cname text-cut">\s*([^<]{2,40})')


def parse_51job_html(html: str, page_url: str) -> list[LeadDraft]:
    drafts: list[LeadDraft] = []
    for block in (html or "").split('class="joblist-item"')[1:]:
        sd_m = _51JOB_SENSORS_RE.search(block)
        if not sd_m:
            continue
        try:
            data = _json.loads(_html.unescape(sd_m.group(1)))
        except ValueError:
            continue
        title = str(data.get("jobTitle") or "").strip()
        comp_m = _51JOB_COMPANY_RE.search(block)
        company = comp_m.group(1).strip() if comp_m else ""
        if not title or not company:
            continue
        area = str(data.get("jobArea") or "")
        city = area.split("·")[0].strip() or None
        # 详情页走 Vue 路由无静态 href，证据 URL 用搜索页（岗位名进 evidence_raw）
        drafts.append(_mk_draft(title, company, city, page_url))
    return drafts


# ---------- 站点注册表（渲染模式下逐站适配） ----------

SITE_CONFIGS: dict[str, dict[str, Any]] = {
    "jobui": {
        "label": "职友集",
        "url": lambda kw, pg: f"{_JOBUl_BASE}/jobs?jobKw={urllib.parse.quote(kw)}&page={pg}",
        "wait": ".c-job-list",
        "parse": parse_jobui_html,
    },
    "liepin": {
        "label": "猎聘",
        "url": lambda kw, pg: f"https://www.liepin.com/zhaopin/?key={urllib.parse.quote(kw)}&pgNo={pg}",
        "wait": '[data-nick="job-detail-job-info"]',
        "parse": parse_liepin_html,
    },
    "51job": {
        "label": "前程无忧",
        "url": lambda kw, pg: (
            f"https://we.51job.com/pc/search?keyword={urllib.parse.quote(kw)}"
            f"&searchType=2&pageNum={pg}"
        ),
        "wait": ".joblist-item",
        "parse": parse_51job_html,
    },
}

# 实测过不了的站（报错信息带原因，避免用户白试）：
STUB_REASONS = {
    "zhipin": "BOSS 直聘无头渲染被 geetest 安全验证页拦截（2026-08-31 实测，需有头浏览器/打码方案）",
    "zhilian": "智联招聘 Security Verification 盾无头过不了（2026-08-31 实测）",
}


async def _render_page(page, url: str, wait_selector: str, timeout_ms: int = 20000) -> str:
    """Playwright 渲染搜索页：等职位卡出现（JS 验证挑战自动通过）后取 HTML。"""
    import asyncio as _aio

    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        await page.wait_for_selector(wait_selector, timeout=timeout_ms // 2)
    except Exception:  # noqa: BLE001  无职位卡（空结果/风控页）也取页面交给解析层判
        await _aio.sleep(1.5)
    return await page.content()


class JobPostingCollector(Collector):
    name = "job_posting"
    title = "中国招聘网站监控（jobui/猎聘/前程无忧）"
    logic_note = (
        "【抓什么】监控中国招聘网站的在招岗位，为库内已有线索的公司补充招聘信号"
        "（在招海外客服=有海外客户、在招 WhatsApp 运营=在用 WA 做私域）。"
        "默认不产生新线索——招聘站公司大多无官网，价值在信号不在发现；"
        "需要扩量时打开『发现新线索』开关。\n"
        "【支持站点】职友集（聚合站）/ 猎聘 / 前程无忧，均为无头浏览器渲染抓取；"
        "BOSS 直聘与智联因验证码拦截暂未接入（选了会明确报错说明原因）。\n"
        "【信号分类】岗位标题自动分五类：WhatsApp 运营/客服、海外客服、海外社媒运营、"
        "CRM 运营、海外销售。分类从严——拿不准的不标，避免把分数抬错。\n"
        "【准确性】同一家公司的多个岗位合并成一条线索（落库前先按域名/电话/公司名+城市"
        "三身份反查去重，绝不重复建线索）；每个岗位帖的链接都存进证据链，可点开核对。"
        "抓取失败的任务直接判失败，不会假装成功。\n"
        "【自动接力】任务完成后系统自动执行「网站富化」——招聘页没有公司官网，"
        "富化会先搜官网再抓信号、重新评分。\n"
        "【建议节奏】配成每天定时跑：新出现的岗位自动并入同一家公司，岗位还在投递也会持续刷新佐证。\n"
        "【边界】站内搜索只认中文岗位词（推荐：跨境电商客服、英语客服、海外社媒运营、私域运营、"
        "外贸业务员、海运客服）；单个词容易被模糊匹配稀释，多词组合效果好。"
    )
    param_schema = [
        {
            "key": "site",
            "label": "招聘网站",
            "required": False,
            "type": "select",
            "options": [
                {"value": "jobui", "label": "职友集（聚合站，覆盖广）"},
                {"value": "liepin", "label": "猎聘"},
                {"value": "51job", "label": "前程无忧"},
            ],
            "default": "jobui",
        },
        {
            "key": "keywords",
            "label": "搜索关键词",
            "required": False,
            "type": "tags",
            "placeholder": "岗位关键词回车，多词组合效果更好（如 跨境电商客服,英语客服,海外社媒运营）",
            # 注意：站内搜索不吃英文词——「whatsapp运营」会联想跑偏到
            # UI 设计师类岗位；单词「海外客服」也可能被稀释成模糊「客服」匹配
            # （2026-08-31 实测单词条 20 岗零信号，多词组合有效）。用中文词组合
            "default": "跨境电商客服,英语客服,海外社媒运营,私域运营,外贸业务员,海运客服",
        },
        {
            "key": "discover_new",
            "label": "发现新线索（默认关）",
            "required": False,
            "type": "switch",
            "placeholder": "默认只给库内已有公司补招聘信号；打开后才作为新线索来源",
            "default": "false",
        },
        # 翻页数不在表单暴露：固定默认 2 页（run() 里兜底），需要调参属于运维场景
    ]

    def validate_params(self, params: dict[str, Any]) -> None:
        site = str(params.get("site") or "jobui").strip()
        if site in STUB_REASONS:
            raise BusinessError(code=40001, message=STUB_REASONS[site])
        if site not in SITE_CONFIGS:
            raise BusinessError(
                code=40001,
                message=f"不支持的站点：{site}（当前支持：{'/'.join(SITE_CONFIGS)}）",
            )

    async def run(self, ctx: TaskContext) -> None:
        site = str(ctx.params.get("site") or "jobui").strip()
        cfg = SITE_CONFIGS[site]
        keywords = split_csv(str(ctx.params.get("keywords"))) or ["whatsapp"]
        # 巡检模式（默认）：只给库内已有公司补招聘信号（career_site 同款口径）；
        # 『发现新线索』开关打开后才作为新线索来源建行
        discover = str(ctx.params.get("discover_new") or "false").lower() in ("1", "true", "yes")
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
            # 不伪装 UA：风控对「伪装 Chrome UA × headless 指纹不一致」打分触发
            # 挑战（jobui 2026-08-31 实测二分确认），Playwright 默认 UA 与浏览器
            # 指纹自洽反而稳定通过
            page = await browser.new_page(
                locale="zh-CN", viewport={"width": 1440, "height": 900}
            )
            try:
                for kw in keywords:
                    for pg in range(1, max_pages + 1):
                        ctx.check_cancelled()
                        url = cfg["url"](kw, pg)
                        await ctx.log("info", f"搜索（{cfg['label']}）：「{kw}」第 {pg} 页")
                        html = None
                        try:
                            html = await _render_page(page, url, cfg["wait"])
                        except Exception as exc:  # noqa: BLE001  渲染失败（挑战/超时）
                            # 连接被重置 = 连续渲染触发站点限流（2026-08-31 验证轮
                            # 实测：~4 页/6s 即被切）：退避 25s 重试一次，仍失败才放弃本页
                            if "ERR_CONNECTION" in str(exc) or "ERR_TIMED_OUT" in str(exc):
                                await ctx.log(
                                    "warn", f"「{kw}」第 {pg} 页被限流（{type(exc).__name__}），退避 25s 重试"
                                )
                                await asyncio.sleep(25)
                                try:
                                    html = await _render_page(page, url, cfg["wait"])
                                except Exception as exc2:  # noqa: BLE001
                                    await ctx.log("error", f"「{kw}」第 {pg} 页重试仍失败：{str(exc2)[:60]}")
                            else:
                                await ctx.log("error", f"「{kw}」第 {pg} 页渲染失败：{type(exc).__name__}: {str(exc)[:60]}")
                        if html is None:
                            continue
                        ok_pages += 1
                        drafts = cfg["parse"](html, url)
                        skipped_offline = 0  # 本页库外公司数（巡检模式跳过计数）
                        for d in drafts:
                            lead_id, _created = await ctx.emit(d, create_if_missing=discover)
                            if lead_id == 0:
                                # 库外公司（巡检模式未命中库内）或空公司名：
                                # 不落信号证据——lead_id=0 没有可挂靠的行
                                if not discover:
                                    skipped_offline += 1
                                continue
                            # 信号级证据（§4.1）：招聘信号带岗位帖 URL 作证据；
                            # 写失败降级 warn 不放大为任务失败（2026-08-31 审计）
                            if d.job_signals:
                                from app.crud.lead_signals import upsert_signal
                                from app.db.session import async_session

                                try:
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
                                except Exception as exc:  # noqa: BLE001
                                    await ctx.log(
                                        "warn",
                                        f"「{d.name}」信号证据写库失败（忽略）：{type(exc).__name__}: {str(exc)[:60]}",
                                    )
                        wa_n = sum(1 for d in drafts if d.whatsapp_job)
                        sig_n = sum(1 for d in drafts if d.job_signals)
                        if discover:
                            head = f"「{kw}」第 {pg} 页 → {len(drafts)} 个在招岗位"
                            if sig_n:
                                head += f"，带海外/运营信号 {sig_n} 个"
                            if wa_n:
                                head += f"（含 WhatsApp 岗位 {wa_n}）"
                        else:
                            # 巡检口径：只报命中库内几家 / 跳过库外几家
                            head = (
                                f"「{kw}」第 {pg} 页 → {len(drafts)} 个在招岗位"
                                f"，命中库内 {len(drafts) - skipped_offline} 家"
                            )
                            if skipped_offline:
                                head += f"，跳过库外 {skipped_offline} 家（巡检模式）"
                        await ctx.log(
                            "info",
                            head
                            + (
                                "——⚠️ 零信号命中：岗位标题与关键词不相关"
                                "（站内搜索会把单词稀释成模糊匹配，建议多词组合跑）"
                                if drafts and not sig_n
                                else ""
                            ),
                        )
                        ctx.inc_progress(1)
                        await asyncio.sleep(_PAGE_GAP)
            finally:
                await browser.close()

        if ok_pages == 0:
            raise BusinessError(
                code=50001,
                message=f"全部页面抓取失败（{cfg['label']} 限流或网络异常）——稍后重跑或降低翻页数",
            )
