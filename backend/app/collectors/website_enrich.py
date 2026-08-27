"""website_enrich 采集器：对库里有网站的线索批量检测 WhatsApp / 邮箱 / 社媒。

不是独立采集源——直接改存量 Lead 行：
    - WhatsApp 插件/链接指纹：wa.me、api.whatsapp.com、ht-ctc / joinchat / getbutton /
      chaty / elfsight 等常见插件
    - 公开邮箱（mailto 优先）、社媒链接（FB/IG/LinkedIn/TG/TikTok）
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import Collector, TaskContext
from app.collectors.normalize import extract_domain
from app.collectors.scoring import compute_score
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
# 命中前 3 条之一的捕获组 → 拿到号码还原标准链接；插件指纹命中则只置标记
_PHONE_PATTERNS = _WHATSAPP_PATTERNS[:3]

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_SOCIAL_RES = [
    ("facebook", re.compile(r"https?://(?:www\.)?facebook\.com/[^\s\"'<>]+", re.I)),
    ("instagram", re.compile(r"https?://(?:www\.)?instagram\.com/[^\s\"'<>]+", re.I)),
    ("linkedin", re.compile(r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[^\s\"'<>]+", re.I)),
    ("telegram", re.compile(r"https?://(?:t\.me|telegram\.me)/[^\s\"'<>]+", re.I)),
    ("tiktok", re.compile(r"https?://(?:www\.)?tiktok\.com/@[^\s\"'<>]+", re.I)),
]
_CONTACT_LINK_RE = re.compile(
    r'href=["\']([^"\']*(?:contact|kontak|about|hubungi)[^"\']*)["\']', re.I
)


async def _fetch(client: httpx.AsyncClient, url: str) -> str | None:
    """抓页面 HTML；任何失败（超时/4xx/5xx/编码异常）返回 None。"""
    try:
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code != 200:
            return None
        return resp.text
    except httpx.HTTPError:
        return None


# 采集 client 必须 trust_env=False：后端进程常继承系统代理（all_proxy），
# 代理对目标站点可能软拦截（实测返回 202），导致「首页抓取失败」误报。
# 采集目标站一律直连；未来需要走代理时显式加 COLLECT_PROXY 配置。
# ponytail: verify=False 仅作为末级兜底 client，只用于读公开页面，可接受
_SSL_LOOSE_CLIENT_ARGS = {"verify": False}


def _make_client(**kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": _UA, "Accept-Language": "en"},
        timeout=_TIMEOUT,
        trust_env=False,
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


_ASSET_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", ".pdf", ".woff", ".woff2", ".ico")


def _is_email(addr: str) -> bool:
    if not _EMAIL_RE.fullmatch(addr):
        return False
    domain = addr.rsplit("@", 1)[1].lower()
    # 防图片/字体文件名误判（如 logo_250x@2x.png）
    return not any(domain.endswith(ext) for ext in _ASSET_EXT)


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
    param_schema = [
        {
            "key": "lead_ids",
            "label": "指定线索 ID（逗号分隔，留空=全库 eligible）",
            "required": False,
            "placeholder": "",
            "default": "",
        },
    ]

    async def run(self, ctx: TaskContext) -> None:
        lead_ids = _parse_lead_ids(ctx.params.get("lead_ids"))
        async with _session_factory()() as session:
            leads = await _load_scope(session, lead_ids)
        if not leads:
            await ctx.log("info", "没有待富化的线索（有网站 且 24h 内未成功富化）")
            return

        ctx.set_total(len(leads))
        await ctx.log("info", f"待富化线索 {len(leads)} 条，并发 {settings.ENRICH_CONCURRENCY}")
        sem = asyncio.Semaphore(settings.ENRICH_CONCURRENCY)
        done = 0

        # 两个 client：正常直连 + 宽松 SSL 兜底（证书过期的小站常见）
        async with _make_client() as client, _make_client(**_SSL_LOOSE_CLIENT_ARGS) as loose:

            async def wrapped(lead_id: int, website: str) -> None:
                nonlocal done
                async with sem:
                    ctx.check_cancelled()
                    await _enrich_one((client, loose), ctx, lead_id, website)
                done += 1
                ctx.inc_progress(1)

            await asyncio.gather(*(wrapped(lid, ws) for lid, ws in leads))

        await ctx.log("info", f"富化完成：{done} 个站点")


async def _load_scope(session: AsyncSession, lead_ids: list[Any]) -> list[tuple[int, str]]:
    """返回 [(lead_id, website)]。lead_ids 指定 → 只取有网站的；否则全库 eligible。"""
    from app.models.lead import Lead

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    if lead_ids:
        stmt = select(Lead.id, Lead.website).where(
            Lead.id.in_(lead_ids), Lead.website.is_not(None), Lead.website != ""
        )
    else:
        stmt = select(Lead.id, Lead.website).where(
            Lead.website.is_not(None),
            Lead.website != "",
            (Lead.enriched_at.is_(None)) | (Lead.enriched_at < cutoff),
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

    # 首页里找联系页链接（最多 2 个，域名相同才跟）
    base_domain = extract_domain(base)
    contact_urls: list[str] = []
    for m in _CONTACT_LINK_RE.finditer(homepage):
        url = _resolve_url(base, m.group(1))
        if url and url not in contact_urls and extract_domain(url) == base_domain:
            contact_urls.append(url)
        if len(contact_urls) >= 2:
            break
    pages = [homepage]
    for url in contact_urls:
        html = await _fetch(primary, url)
        if html:
            pages.append(html)

    whatsapp_hit, whatsapp_url = detect_whatsapp(pages)
    email = detect_email(homepage)
    social = detect_social(pages)

    async with _session_factory()() as session:
        from app.models.lead import Lead

        lead = await session.get(Lead, lead_id)
        if lead is None:
            return
        if whatsapp_hit:
            lead.whatsapp_hit = True
            if whatsapp_url:
                lead.whatsapp_url = whatsapp_url
        if email and not lead.email:
            lead.email = email
        if social:
            merged = dict(lead.social or {})
            merged.update(social)
            lead.social = merged
        lead.enriched_at = datetime.now(timezone.utc)
        lead.score, lead.score_signals = compute_score(
            whatsapp_hit=lead.whatsapp_hit,
            whatsapp_job=lead.whatsapp_job,
            website=lead.website,
            email=lead.email,
            country=lead.country,
            phone_raw=lead.phone_raw,
            phone_e164=lead.phone_e164,
            social=lead.social,
        )
        await session.commit()
    if whatsapp_hit:
        await ctx.log("info", f"[lead {lead_id}] ✅ 检测到 WhatsApp：{website}")
