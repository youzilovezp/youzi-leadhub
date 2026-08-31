"""website_enrich 采集器：对库里有网站的线索批量检测 WhatsApp / 邮箱 / 社媒 / 场景 / SaaS 需求。

不是独立采集源——直接改存量 Lead 行：
    - WhatsApp 插件/链接指纹：wa.me、api.whatsapp.com、ht-ctc / joinchat / getbutton /
      chaty / elfsight 等常见插件
    - 公开邮箱（mailto 优先）、社媒链接（FB/IG/LinkedIn/TG/TikTok）
    - WhatsApp 场景（客服/营销/交易/SaaS）与 SaaS 需求信号（CRM/工单/Chatbot…）
      的关键词识别（collectors/scenes.py）
    - 抓到公开邮箱 → 自动生成「待补全」联系人（crud/contact.py）
    - 每站点最多 3 个请求（首页 + 2 个联系页）
    - 24h 跳过以成功为准：只在富化成功时写 enriched_at，失败/超时下次重跑

任务范围（文档定的）：params.lead_ids 指定（列表勾选入口）；不指定 = 全库
「有网站 且 24h 内未成功富化」。
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import Collector, TaskContext
from app.collectors.normalize import extract_domain
from app.collectors.overseas import detect_domain_tld, detect_overseas_signals
from app.collectors.scenes import (
    SAAS_LABELS_ZH,
    SCENE_LABELS_ZH,
    detect_saas_signals,
    detect_scenes,
    page_text,
)
from app.core.config import settings

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_TIMEOUT = httpx.Timeout(15.0, connect=10.0)

# WhatsApp 指纹：直链 + 常见 WordPress/SAAS 聊天插件特征串
_WHATSAPP_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(?:https?://)?wa\.me/(\+?\d{6,15})",
        r"(?:https?://)?api\.whatsapp\.com/send[^\s\"'<>]*?phone=(\+?\d{6,15})",
        r"wp\.whatsapp\.com/send[^\s\"'<>]*?phone=(\+?\d{6,15})",
        r"whatsapp[^a-z0-9]{0,20}(?:send|chat|message)",
        r"(?:ht-ctc|joinchat|getbutton|chaty|elfsight|click-to-chat|whatsapp-chat)",
    )
]
# 群组邀请链接（PRD §4.1）：chat.whatsapp.com/xxx = 已在运营 WhatsApp 社群（私域证据）
_GROUP_LINK_RE = re.compile(r"https?://chat\.whatsapp\.com/[A-Za-z0-9_-]{5,}")
# 命中前 3 条之一的捕获组 → 拿到号码还原标准链接；插件指纹命中则只置标记
_PHONE_PATTERNS = _WHATSAPP_PATTERNS[:3]
# 插件特征（第 5 条）：ht-ctc/joinchat/getbutton/chaty/elfsight/click-to-chat
_PLUGIN_PATTERNS = _WHATSAPP_PATTERNS[4:]

# WhatsApp Business 使用代理判定（§4.1「号码类型/入口形态」）：页面自述在用
# WhatsApp Business（业务号而非个人号）——文本或类属性出现即认为命中
_WA_BUSINESS_RES = [
    re.compile(r"whatsapp\s+business", re.I),
    re.compile(r"wa\s+business\s+(?:account|number|api)", re.I),
    re.compile(r"whatsapp\s*商业号|whatsapp\s*企业号", re.I),
]

# ICP 备案号（中国网站强证据）：页脚「京ICP备12345678号-1」等——纯英文站的
# 中国出海企业常保留备案信息，是防误杀的关键 CN 证据
_ICP_LICENSE_RE = re.compile(
    r"[京沪津渝冀晋蒙辽吉黑苏浙皖闽赣鲁豫鄂湘粤桂琼川黔滇藏陕甘青宁新港澳]"
    r"ICP[备证]\s*\d{6,10}\s*号(?:-\d{1,3})?"
)


def detect_icp_license(html_list: list[str | None]) -> str | None:
    """页面是否含 ICP 备案号（中国企业强证据）。返回命中的备案号原文。"""
    joined = "\n".join(h for h in html_list if h)
    if not joined:
        return None
    m = _ICP_LICENSE_RE.search(joined)
    return m.group(0) if m else None


def detect_cn_content(html_list: list[str | None]) -> bool:
    """页面是否以中文为主（中国企业官网特征）：可见文本 CJK 占比 ≥ 30%。"""
    joined = page_text(html_list)
    if not joined:
        return False
    cjk = sum(1 for ch in joined if "一" <= ch <= "鿿")
    return cjk / len(joined) >= 0.30


def detect_wa_business(html_list: list[str | None]) -> bool:
    """页面是否自述使用 WhatsApp Business（业务号）。"""
    joined = "\n".join(h for h in html_list if h)
    if not joined:
        return False
    return any(rx.search(joined) for rx in _WA_BUSINESS_RES)


def detect_whatsapp_groups(html_list: list[str | None]) -> list[str]:
    """页面里的 WhatsApp 群邀请链接（去重保序）——私域运营证据。"""
    joined = "\n".join(h for h in html_list if h)
    groups: list[str] = []
    for m in _GROUP_LINK_RE.finditer(joined):
        url = m.group(0)
        if url not in groups:
            groups.append(url)
    return groups

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_SOCIAL_RES = [
    ("facebook", re.compile(r"https?://(?:www\.)?facebook\.com/[^\s\"'<>]+", re.I)),
    ("instagram", re.compile(r"https?://(?:www\.)?instagram\.com/[^\s\"'<>]+", re.I)),
    ("linkedin", re.compile(r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[^\s\"'<>]+", re.I)),
    ("telegram", re.compile(r"https?://(?:t\.me|telegram\.me)/[^\s\"'<>]+", re.I)),
    ("tiktok", re.compile(r"https?://(?:www\.)?tiktok\.com/@[^\s\"'<>]+", re.I)),
    ("youtube", re.compile(r"https?://(?:www\.)?youtube\.com/(?:c|channel|user|@)[^\s\"'<>]+", re.I)),
]
# Contact/About/Products 三类内页（官网四层抓取：首页 + 联系/关于/产品页）。
# Products 层是 B2B/B2C/品类与交易场景关键词的主要来源（跨境电商站尤其）
_INNER_PAGE_LINK_RE = re.compile(
    r'href=["\']([^"\']*(?:contact|kontak|about|hubungi|product|shop|store|catalog|collection|faq|help)[^"\']*)["\']',
    re.I,
)
# 内页抓取上限（首页 + 最多 3 个内页；原来只 2 个联系页）
_MAX_INNER_PAGES = 3


async def _fetch(client: httpx.AsyncClient, url: str) -> str | None:
    """抓页面 HTML；任何失败（超时/4xx/5xx/编码异常）返回 None。"""
    try:
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code != 200:
            return None
        return resp.text
    except httpx.HTTPError:
        return None


# 采集 client 双通道（与 meta_ads / web_search 同策略）：
# - 主通道走系统代理（出海官网多在海外/CDN 后，国内直连不可达——实测
#   shein.com/banggood.com 直连超时）；未配代理环境 = 等效直连
# - 兜底通道强制直连 + 宽松 SSL（防代理对目标站软拦截返回 202 的误报，
#   primal.com.ph 案例；证书过期的小站也能抓）
_SSL_LOOSE_CLIENT_ARGS = {"verify": False, "trust_env": False}


def _make_client(**kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": _UA, "Accept-Language": "en"},
        timeout=_TIMEOUT,
        **kwargs,
    )


async def _fetch_site(clients: tuple[httpx.AsyncClient, httpx.AsyncClient], url: str) -> str | None:
    """抓站点首页，失败时换 scheme 重试，最后用宽松 SSL 的兜底 client 再试一次。

    请求预算：正常 1 次；失败最多 +2 次（换 scheme、宽松 SSL）。
    联系页抓取不算在内（首页成功才有联系页），礼貌性可控。
    """
    primary, loose = clients
    html = await _fetch(primary, url)
    if html is not None:
        return html
    alt = url.replace("https://", "http://", 1) if url.startswith("https://") else url.replace(
        "http://", "https://", 1
    )
    if alt != url:
        html = await _fetch(primary, alt)
        if html is not None:
            return html
    return await _fetch(loose, url)


def _resolve_url(base_url: str, href: str) -> str | None:
    if href.startswith(("http://", "https://")):
        return href
    try:
        return str(httpx.URL(base_url).join(href))
    except Exception:  # noqa: BLE001  相对链接拼接失败不致命
        return None


def detect_whatsapp(html_list: list[str]) -> tuple[bool, str | None]:
    """返回 (是否命中, 标准化 wa.me 链接或 None)。"""
    joined = "\n".join(h for h in html_list if h)
    if not joined:
        return False, None
    for pattern in _PHONE_PATTERNS:
        m = pattern.search(joined)
        if m and m.group(1):
            return True, f"https://wa.me/{m.group(1).lstrip('+')}"
    for pattern in _WHATSAPP_PATTERNS:
        if pattern.search(joined):
            return True, None
    return False, None


def detect_whatsapp_numbers(html_list: list[str]) -> list[str]:
    """页面里出现的全部 WhatsApp 号码（去重保序）——「多分线 = 已规模化使用」证据（§4.1）。"""
    joined = "\n".join(h for h in html_list if h)
    numbers: list[str] = []
    for pattern in _PHONE_PATTERNS:
        for m in pattern.finditer(joined):
            raw = (m.group(1) or "").lstrip("+")
            if raw and raw not in numbers:
                numbers.append(raw)
    return numbers


_ASSET_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", ".pdf", ".woff", ".woff2", ".ico")
# 平台埋点/监控邮箱误报黑名单（实测 Wix 站点正文里带 Sentry 埋点邮箱）
_EMAIL_DOMAIN_BLOCKLIST = ("wixpress.com", "sentry.io", "sentry-next.com", "googlegroups.com")


def _is_email(addr: str) -> bool:
    if not _EMAIL_RE.fullmatch(addr):
        return False
    domain = addr.rsplit("@", 1)[1].lower()
    # 防图片/字体文件名误判（如 logo_250x@2x.png）
    if any(domain.endswith(ext) for ext in _ASSET_EXT):
        return False
    return not any(domain == d or domain.endswith("." + d) for d in _EMAIL_DOMAIN_BLOCKLIST)


def detect_email(homepage_html: str | None) -> str | None:
    """mailto: 优先，其次正文邮箱正则。"""
    if not homepage_html:
        return None
    for m in re.finditer(r'href=["\']mailto:([^"\'>]+)', homepage_html, re.I):
        addr = m.group(1).strip()
        if _is_email(addr):
            return addr
    for m in _EMAIL_RE.finditer(homepage_html):
        if _is_email(m.group(0)):
            return m.group(0)
    return None


def detect_social(html_list: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    joined = "\n".join(h for h in html_list if h)
    for platform, pattern in _SOCIAL_RES:
        m = pattern.search(joined)
        if m:
            found[platform] = m.group(0)
    return found


class WebsiteEnrichCollector(Collector):
    name = "website_enrich"
    title = "网站富化（检测 WhatsApp/邮箱/社媒）"
    logic_note = (
        "【做什么】给线索补齐关键信息：没有官网的先搜出官网，再抓官网页面，"
        "识别 WhatsApp 入口和号码、联系邮箱、社交媒体、业务场景（客服/营销/订单）、"
        "SaaS 工具需求（CRM/工单/Chatbot）、出海证据（多语言/海外货币/国际物流/投放市场）、"
        "ICP 备案号。识别完自动重新评分。\n"
        "【准确性】只在成功打开官网时才记录结果，打不开的下一轮自动重试，不出假数据；"
        "邮箱识别带两层防误判（排除图片文件名、网站监控埋点邮箱）；"
        "判断「是不是中国企业」优先认 ICP 备案号，其次看中文内容占比。\n"
        "【循环复核】已入库的线索按等级定期重查：S 级每天、A 级每 3 天、B 级每 7 天、"
        "C 级每 30 天。每次重查都会刷新信号的最近确认时间，公司撤了 WhatsApp 按钮也能从"
        "证据时间上看出来。\n"
        "【怎么运行】不用手动建任务：搜索、招聘监控、广告库任何一个跑完，系统会自动执行本步骤；"
        "在线索列表勾选线索点「检测 WhatsApp」也会立即对选中线索执行。"
    )
    param_schema = [
        {
            "key": "lead_ids",
            "label": "指定线索 ID",
            "required": False,
            "type": "tags",
            "placeholder": "留空 = 全库待富化线索；输入 ID 回车",
            "default": "",
        },
    ]

    async def run(self, ctx: TaskContext) -> None:
        lead_ids = _parse_lead_ids(ctx.params.get("lead_ids"))
        async with _session_factory()() as session:
            leads = await _load_scope(session, lead_ids)

        # 两个 client：正常（代理优先）+ 宽松 SSL 直连兜底（证书过期的小站常见）
        async with _make_client() as client, _make_client(**_SSL_LOOSE_CLIENT_ARGS) as loose:
            # ---------- 官网发现（补全链）：无官网线索先搜官网再富化 ----------
            # 招聘站（jobui）公司页无官网字段——缺官网的线索进不了富化/评分链路，
            # cn_domestic 永远升不了 qualified。仅全库扫描模式做（手动勾选是精确富化）
            discovered: list[tuple[int, str]] = []
            if not lead_ids:
                async with _session_factory()() as session:
                    candidates = await _load_discoverable(session, _DISCOVER_LIMIT)
                for lid, name in candidates:
                    ctx.check_cancelled()
                    ws = await _discover_website((client, loose), name)
                    if ws:
                        from app.crud.lead import touch_field_meta
                        from app.models.lead import Lead

                        async with _session_factory()() as session:
                            lead = await session.get(Lead, lid)
                            if lead and not lead.website:
                                lead.website = ws
                                lead.domain = extract_domain(ws) or lead.domain
                                touch_field_meta(
                                    lead, "website", "web_discovery",
                                    confidence=60, now=datetime.now(timezone.utc),
                                )
                                await session.commit()
                                discovered.append((lid, ws))
                                await ctx.log("info", f"[lead {lid}] 🔍 官网发现：{name} → {ws}")
                    await asyncio.sleep(_DISCOVER_GAP)  # 搜索礼貌间隔
                if candidates:
                    await ctx.log(
                        "info", f"官网发现：{len(discovered)}/{len(candidates)} 条命中"
                    )
            leads = [*discovered, *leads]

            if not leads:
                await ctx.log("info", "没有待富化的线索（有网站 或 已尝试发现，且窗口内未成功富化）")
                return

            ctx.set_total(len(leads))
            await ctx.log("info", f"待富化线索 {len(leads)} 条，并发 {settings.ENRICH_CONCURRENCY}")
            sem = asyncio.Semaphore(settings.ENRICH_CONCURRENCY)
            done = 0

            async def wrapped(lead_id: int, website: str) -> None:
                nonlocal done
                async with sem:
                    ctx.check_cancelled()
                    try:
                        await _enrich_one((client, loose), ctx, lead_id, website)
                    except Exception:  # noqa: BLE001  单站点失败不放大为整任务失败
                        logger.exception(f"[lead {lead_id}] 富化异常：{website}")
                done += 1
                ctx.inc_progress(1)

            await asyncio.gather(*(wrapped(lid, ws) for lid, ws in leads))

        await ctx.log("info", f"富化完成：{done} 个站点")


# ---------- 官网发现（补全链，2026-08-31） ----------
# 招聘站（jobui）公司页无官网字段——缺官网的线索进不了富化/评分链路，
# cn_domestic 永远升不了 qualified。用公司名走搜索引擎（默认引擎、零 key）
# 找官网，复用 web_search 的平台/文章页过滤与根 URL 归一。
_DISCOVER_GAP = 2.0  # 搜索礼貌间隔（秒）
_DISCOVER_LIMIT = 30  # 每次任务最多发现的线索数（搜索配额友好）


async def _discover_website(
    clients: tuple[httpx.AsyncClient, httpx.AsyncClient], name: str
) -> str | None:
    """公司名 → 官网（第一个企业站结果，根 URL 归一）。找不到返回 None。"""
    from app.collectors.web_search import _search, results_to_drafts

    items, _err = await _search(clients, settings.SEARCH_ENGINE, f"{name} 官网", 5)
    if not items:
        return None
    drafts = results_to_drafts(items, params_is_cn=True)
    return drafts[0].website if drafts else None


async def _load_discoverable(session: AsyncSession, limit: int) -> list[tuple[int, str]]:
    """无官网且非 foreign 的线索（分数倒序——高分商机优先补全）。"""
    from app.models.lead import Lead

    stmt = (
        select(Lead.id, Lead.name)
        .where((Lead.website.is_(None)) | (Lead.website == ""), Lead.icp_status != "foreign")
        .order_by(Lead.score.desc())
        .limit(limit)
    )
    return [(r[0], r[1]) for r in (await session.execute(stmt)).all()]


async def _load_scope(session: AsyncSession, lead_ids: list[Any]) -> list[tuple[int, str]]:
    """返回 [(lead_id, website)]。lead_ids 指定 → 只取有网站的；否则全库 eligible。"""
    from app.models.lead import Lead

    now = datetime.now(timezone.utc)
    if lead_ids:
        # 手动勾选不受刷新窗口限制
        stmt = select(Lead.id, Lead.website).where(
            Lead.id.in_(lead_ids), Lead.website.is_not(None), Lead.website != ""
        )
    else:
        # 分级增量重爬（补充需求 §九）：高价值线索检查更勤——S 每天 / A 3 天 / B 7 天 / C 30 天；
        # ENRICH_INTERVAL_HOURS 作为 C 级（兜底档）的可配置上限。
        # foreign 行不在服务范围（ICP 门已排除销售池），不消耗抓取配额
        from sqlalchemy import or_

        def _stale(grade: str, days: int):
            cutoff = now - timedelta(days=days)
            return (Lead.grade == grade) & (
                (Lead.enriched_at.is_(None)) | (Lead.enriched_at < cutoff)
            )

        c_days = max(1, settings.ENRICH_INTERVAL_HOURS // 24)
        stmt = select(Lead.id, Lead.website).where(
            Lead.website.is_not(None),
            Lead.website != "",
            Lead.icp_status != "foreign",
            or_(_stale("S", 1), _stale("A", 3), _stale("B", 7), _stale("C", c_days)),
        )
    rows = (await session.execute(stmt)).all()
    return [(r[0], r[1]) for r in rows]


def _parse_lead_ids(raw: Any) -> list[int]:
    """lead_ids 参数归一化：勾选入口传 list[int]；手动任务表单传的是字符串
    "12,34"——直接喂给 in_() 会被 SQLAlchemy 拒（ArgumentError），任务 failed。
    非法项丢弃（宁漏勿错富化）。
    """
    if not raw:
        return []
    items = raw if isinstance(raw, list | tuple) else str(raw).split(",")
    out: list[int] = []
    for x in items:
        try:
            out.append(int(str(x).strip()))
        except (TypeError, ValueError):
            continue
    return out


def _session_factory():
    from app.db.session import async_session

    return async_session


async def _enrich_one(
    clients: tuple[httpx.AsyncClient, httpx.AsyncClient], ctx: TaskContext, lead_id: int, website: str
) -> None:
    """富化单个站点并回写。只在「成功抓到至少一个页面」时更新 enriched_at。"""
    primary = clients[0]
    base = website if website.startswith(("http://", "https://")) else f"https://{website}"
    homepage = await _fetch_site(clients, base)
    if homepage is None:
        await ctx.log("warn", f"[lead {lead_id}] 首页抓取失败：{website}")
        return

    # 首页里找内页链接（联系/关于/产品，最多 3 个，域名相同才跟）
    base_domain = extract_domain(base)
    inner_urls: list[str] = []
    for m in _INNER_PAGE_LINK_RE.finditer(homepage):
        url = _resolve_url(base, m.group(1))
        if url and url not in inner_urls and extract_domain(url) == base_domain:
            inner_urls.append(url)
        if len(inner_urls) >= _MAX_INNER_PAGES:
            break
    pages = [homepage]
    page_urls = [base]  # 与 pages 平行：每页 HTML 对应的 URL（证据链用）
    for url in inner_urls:
        html = await _fetch(primary, url)
        if html:
            pages.append(html)
            page_urls.append(url)

    whatsapp_hit, whatsapp_url = detect_whatsapp(pages)
    wa_numbers = detect_whatsapp_numbers(pages)
    email = detect_email(homepage)
    social = detect_social(pages)
    scenes = detect_scenes(pages)
    saas_signals = detect_saas_signals(pages)
    # 出海信号（PRD §4.2）：货币/多语言/电商栈/配送/市场/出海自述/海外域名
    overseas = detect_overseas_signals(pages)
    tld = detect_domain_tld(base)
    if tld:
        overseas.setdefault("domain_tld", []).append(tld)
    # WhatsApp Business 使用（§4.1）+ 群组链接（§4.1 私域证据）
    wa_business = detect_wa_business(pages)
    wa_groups = detect_whatsapp_groups(pages)

    async with _session_factory()() as session:
        from app.crud.contact import auto_create_from_email
        from app.crud.lead import touch_field_meta
        from app.crud.lead_events import rescore_and_log, snapshot_lead
        from app.models.lead import Lead

        lead = await session.get(Lead, lead_id)
        if lead is None:
            return
        before = snapshot_lead(lead)
        now = datetime.now(timezone.utc)
        if whatsapp_hit:
            lead.whatsapp_hit = True
            if whatsapp_url:
                lead.whatsapp_url = whatsapp_url
            # WhatsApp 检测来源=官网，置信度高（§32）
            touch_field_meta(lead, "whatsapp_url", "website_enrich", confidence=98, now=now)
        if wa_numbers:
            # 多号码证据链（§4.1）：Sales/Support 分线 = 规模化私域，只增不减
            merged_numbers = list(lead.whatsapp_numbers or [])
            for n in wa_numbers:
                if n not in merged_numbers:
                    merged_numbers.append(n)
            lead.whatsapp_numbers = merged_numbers
            touch_field_meta(
                lead, "whatsapp_numbers", "website_enrich", confidence=95, now=now
            )
        if email and not lead.email:
            lead.email = email
            touch_field_meta(lead, "email", "website_enrich", confidence=90, now=now)
        if social:
            merged = dict(lead.social or {})
            merged.update(social)
            lead.social = merged
            touch_field_meta(lead, "social", "website_enrich", confidence=95, now=now)
        if scenes:
            merged_scenes = list(lead.scenes or [])
            for s in scenes:
                if s not in merged_scenes:
                    merged_scenes.append(s)
            lead.scenes = merged_scenes  # 重新赋值触发 JSON 变更追踪
            touch_field_meta(lead, "scenes", "website_enrich", confidence=80, now=now)
        if saas_signals:
            merged_saas = dict(lead.saas_signals or {})
            for k, v in saas_signals.items():
                merged_saas[k] = max(merged_saas.get(k, 0), v)
            lead.saas_signals = merged_saas
            touch_field_meta(lead, "saas_signals", "website_enrich", confidence=75, now=now)
        if wa_business and not lead.wa_business:
            lead.wa_business = True
            touch_field_meta(lead, "wa_business", "website_enrich", confidence=75, now=now)
        # 中国企业证据（ICP 二重门·门1）：ICP 备案号 > 中文内容占比——
        # 纯英文站的中国出海企业常保留备案号，是防误杀为 foreign 的关键证据
        icp_license = detect_icp_license(pages)
        if icp_license and not lead.is_cn:
            lead.is_cn = True
            touch_field_meta(lead, "is_cn", "website_enrich", confidence=98, now=now)
        elif not lead.is_cn and detect_cn_content(pages):
            # 官网含显著中文内容——中文站服务海外市场 = 出海企业
            lead.is_cn = True
            touch_field_meta(lead, "is_cn", "website_enrich", confidence=85, now=now)
        if overseas:
            merged_ov = dict(lead.overseas_signals or {})
            for k, vals in overseas.items():
                bucket = list(merged_ov.get(k) or [])
                for v in vals or []:
                    if v not in bucket:
                        bucket.append(v)
                merged_ov[k] = bucket
            lead.overseas_signals = merged_ov
            touch_field_meta(lead, "overseas_signals", "website_enrich", confidence=85, now=now)
        # ---------- 信号级证据链（§4.1：类型/值/来源页面/原文/置信度/时间） ----------
        from app.crud.lead_signals import upsert_signal

        # 群邀请链接 = 社群私域运营证据（§4.1/§4.4-E）
        for g in wa_groups:
            await upsert_signal(
                session, lead.id, "whatsapp_group", g,
                source="website_enrich", evidence_url=base,
                evidence_raw=g, confidence=90,
            )
        if wa_business:
            await upsert_signal(
                session, lead.id, "wa_business", "WhatsApp Business",
                source="website_enrich", evidence_url=base,
                evidence_raw="页面自述使用 WhatsApp Business", confidence=75,
            )
        if icp_license:
            # CN 证据入证据链：销售可见"为什么判定是中国企业"
            await upsert_signal(
                session, lead.id, "cn_icp", icp_license,
                source="website_enrich", evidence_url=base,
                evidence_raw=f"页脚备案号：{icp_license}", confidence=98,
            )

        for i, page_html in enumerate(pages):
            page_url = page_urls[i] if i < len(page_urls) else base
            for pat in _PHONE_PATTERNS:
                for m in pat.finditer(page_html or ""):
                    raw = (m.group(1) or "").lstrip("+")
                    if raw:
                        await upsert_signal(
                            session, lead.id, "whatsapp_number", raw,
                            source="website_enrich", evidence_url=page_url,
                            evidence_raw=m.group(0), confidence=95,
                        )
            for pat in _PLUGIN_PATTERNS:
                m = pat.search(page_html or "")
                if m:
                    await upsert_signal(
                        session, lead.id, "whatsapp_plugin", "detected",
                        source="website_enrich", evidence_url=page_url,
                        evidence_raw=m.group(0)[:200], confidence=75,
                    )
        if whatsapp_url:
            await upsert_signal(
                session, lead.id, "whatsapp_link", whatsapp_url,
                source="website_enrich", evidence_url=base,
                evidence_raw=whatsapp_url, confidence=98,
            )
        _OV_TYPE_MAP = {
            "currencies": ("overseas_currency", 85),
            "languages": ("multilang", 80),
            "ecommerce": ("ecommerce_stack", 90),
            "shipping": ("intl_shipping", 80),
            "markets": ("market_mention", 75),
            "export_words": ("export_word", 75),
        }
        for ov_key, vals in (overseas or {}).items():
            sig_type, conf = _OV_TYPE_MAP.get(ov_key, (ov_key, 75))
            for v in vals or []:
                await upsert_signal(
                    session, lead.id, sig_type, str(v),
                    source="website_enrich", evidence_url=base,
                    evidence_raw=str(v)[:200], confidence=conf,
                )
        if email:
            # 抓到公开邮箱 → 自动生成「待补全」联系人（同邮箱已存在则跳过）
            await auto_create_from_email(session, lead, email)
        lead.enriched_at = now
        # 统一重评钩子：六维重算（读 ORM 行属性，fb_whatsapp 不再漏传）+ 事件发射
        await rescore_and_log(session, lead, before=before)
        await session.commit()
    if whatsapp_hit:
        await ctx.log("info", f"[lead {lead_id}] ✅ 检测到 WhatsApp：{website}")
    if scenes:
        labels = "、".join(SCENE_LABELS_ZH.get(s, s) for s in scenes)
        await ctx.log("info", f"[lead {lead_id}] 场景命中：{labels}")
    if saas_signals:
        labels = "、".join(SAAS_LABELS_ZH.get(k, k) for k in saas_signals)
        await ctx.log("info", f"[lead {lead_id}] SaaS 需求信号：{labels}")
