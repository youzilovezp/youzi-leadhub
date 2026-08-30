"""meta_ads 采集器：Meta 广告资料库（Ad Library API）→ 中国出海企业商机。

ICP 反转后的主通道：我们卖 WhatsApp Business API，客户是「做海外生意的中国企业」，
不是海外本地商家。谁在投 CTWA / 做海外私域，谁就是高意向人群：

    广告库按关键词搜「在投广告 × 投放目标市场」→ 广告主（FB 主页）去重
    → 逐个探测主页 HTML：wa.me 按钮（私域入口，白得 E.164 手机号）、
      联系邮箱、外链官网、中文特征
    → emit LeadDraft（is_cn / fb_whatsapp 信号驱动评分与筛选）

网络：Meta 域国内直连被墙——双通道（系统代理优先，连接失败/软拦截切直连兜底），
与 osm_overpass 同策略。

CTWA 说明：Ad Library API 不暴露广告 CTA 类型（接口限制），「主页带 wa.me 按钮
+ 持续在投广告」是该企业已用 WhatsApp 做私域获客的可靠代理信号；主页探测还能
直接拿到对方的 WhatsApp 号码——销售建联的第一入口。

凭据：META_ADS_ACCESS_TOKEN（.env），免费申请：
https://www.facebook.com/ads/archive/api （只需 ads_archive 只读公开数据权限）
"""

from __future__ import annotations

import asyncio
import re
import urllib.parse
from typing import Any

import httpx

from app.collectors.base import (
    COUNTRY_OPTIONS,
    Collector,
    LeadDraft,
    TaskContext,
    require_params,
    split_csv,
)
from app.collectors.website_enrich import detect_email, detect_whatsapp, detect_whatsapp_numbers
from app.core.config import settings
from app.core.exceptions import BusinessError

_GRAPH_VERSION = "v22.0"  # Meta 版本两年弃用窗口，过期改这里
_ADS_URL = f"https://graph.facebook.com/{_GRAPH_VERSION}/ads_archive"
_ADS_FIELDS = (
    "id,page_id,page_name,page_profile_uri,"
    "ad_creative_bodies,ad_creative_link_captions,ad_delivery_start_time,"
    "ad_reached_countries"  # 出海画像（§8）：累计每个广告主实际投放的国家
)
_PAGE_SIZE = 100  # API 上限
_PROBE_DELAY = 1.5  # 主页探测节流（秒）：对 FB 礼貌一点，别触发风控
_API_DELAY = 0.5  # 翻页间隔
_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

# wa.me/60123456789 或 api.whatsapp.com/send?phone=60123456789 → 直接抠号码
_WA_PHONE_RES = (
    re.compile(r"wa\.me/(\d{6,15})"),
    re.compile(r"api\.whatsapp\.com/send\?[^\"'\s<>]*?phone=(\d{6,15})"),
)
# FB 页里外链常包一层 l.facebook.com/l.php?u=<urlencode>
_FB_REDIRECT_RE = re.compile(r"l\.facebook\.com/l\.php\?u=([^\"'\s&]+)")
_URL_RE = re.compile(r"https?://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:/[^\s\"'<>]*)?")
# 这些域不算「官网」
_NON_SITE_DOMAINS = (
    "facebook.com", "fb.com", "fb.me", "fb.watch", "messenger.com",
    "instagram.com", "whatsapp.com", "wa.me", "threads.net",
    "google.com", "goo.gl", "apple.com", "play.google.com",
    "bit.ly", "tinyurl.com",
)
_CJK_RE = re.compile(r"[一-鿿]")
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# ---------- 只保留公司/企业：主页类目识别与过滤 ----------
# FB 页面 HTML 的 JSON 块里有类目标签："categoryName":"Shopping & Retail" / 「购物广场」等
_CATEGORY_RES = (
    re.compile(r'"categoryName"\s*:\s*"([^"]{1,64})"'),
    re.compile(r'"category"\s*:\s*"([^"]{1,64})"'),
)

# 非企业类目关键词（小写子串匹配；命中即跳过）。公众人物/自媒体/媒体/政府/社区不是我们的买家。
_NON_COMPANY_KEYWORDS = (
    # 英文（FB 常见类目）
    "public figure", "publicfigure", "artist", "musician", "musician/band", "band",
    "politician", "political", "athlete", "actor", "actress", "writer", "author",
    "photographer", "model", "personal blog", "blogger", "just for fun", "community",
    "government", "government official", "news", "media", "tv channel", "radio station",
    "journalist", "producer", "director", "comedian", "dancer", "chef",
    "education website", "school", "university", "college", "non-profit", "nonprofit",
    "religious", "church", "sports team", "sports league", "movie", "theatre", "arts",
    # 中文
    "公众人物", "个人博主", "自媒体", "艺术家", "音乐人", "乐队", "歌手", "演员", "作家",
    "模特", "摄影师", "政治人物", "运动员", "社区", "政府", "新闻", "媒体", "电视台",
    "电台", "记者", "导演", "喜剧演员", "舞者", "学校", "大学", "非营利", "宗教", "教会",
    "球队", "联盟", "电影", "剧院", "艺术",
)

# 常见 FB 企业类目 → 中文（存进 Lead.industry，列表筛选/展示直接可读；未收录原样保留）
_FB_CATEGORY_ZH = {
    "shopping & retail": "零售",
    "shopping mall": "购物中心",
    "clothing store": "服装店",
    "clothing (brand)": "服装品牌",
    "shoes store": "鞋店",
    "jewelry/watches": "珠宝手表",
    "jewelry & watches store": "珠宝手表",
    "electronics": "电子产品",
    "phone/tablet": "手机数码",
    "home improvement": "家居改善",
    "furniture store": "家具店",
    "beauty salon": "美容院",
    "health/beauty": "健康美容",
    "medical & health": "医疗健康",
    "dentist": "牙科",
    "restaurant": "餐饮",
    "food & beverage": "食品饮料",
    "grocery store": "杂货店",
    "company": "公司企业",
    "business service": "商业服务",
    "marketing agency": "营销代理",
    "advertising agency": "广告代理",
    "e-commerce website": "电商网站",
    "online marketplace": "线上市场",
    "travel company": "旅游公司",
    "hotel": "酒店",
    "education": "教育",
    "toys store": "玩具店",
    "pet store": "宠物店",
    "sports goods store": "体育用品店",
    "auto parts store": "汽配店",
    "car dealer": "汽车经销商",
    "real estate": "房地产",
    "legal service": "法律服务",
    "finance": "金融",
    "logistics service": "物流服务",
    "manufacturer": "制造业",
    "industrial company": "工业企业",
    "wholesale & supply": "批发供应",
    "shopping & fashion": "时尚零售",
    "baby goods/kids goods": "母婴用品",
    "bags/luggage": "箱包",
}


def _extract_category(html: str) -> str | None:
    """从 FB 主页 HTML 抠类目标签（页面 JSON 块里的 categoryName）。"""
    for rx in _CATEGORY_RES:
        m = rx.search(html)
        if m:
            return m.group(1)
    return None


def _is_company_category(category: str | None) -> bool:
    """类目是否企业（None 拿不到类目时保守放行——登录墙页面常无类目信息）。"""
    if not category:
        return True
    lowered = category.lower()
    return not any(k in lowered for k in _NON_COMPANY_KEYWORDS)


def _extract_wa_phone(html: str) -> str | None:
    """从页面 HTML 的 WhatsApp 链接里抠出号码（已是完整国际号，不含 +）。"""
    for rx in _WA_PHONE_RES:
        m = rx.search(html)
        if m:
            return m.group(1)
    return None


def _extract_site(html: str) -> str | None:
    """从 FB 主页 HTML 找外链官网：解 l.php 跳转 + 裸链接，滤掉平台域。"""
    candidates: list[str] = []
    for m in _FB_REDIRECT_RE.finditer(html):
        try:
            candidates.append(urllib.parse.unquote(m.group(1)))
        except Exception:  # noqa: BLE001  解码失败跳过
            continue
    candidates.extend(m.group(0) for m in _URL_RE.finditer(html))
    for url in candidates:
        host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
        if host and not any(host == d or host.endswith("." + d) for d in _NON_SITE_DOMAINS):
            return url
    return None


def _looks_cn(texts: list[str | None]) -> bool:
    """中国出海特征：品牌名或广告文案含中文（跨境大卖的素材普遍双语）。"""
    return any(t and _CJK_RE.search(t) for t in texts)


async def _ads_get(
    clients: tuple[httpx.AsyncClient, httpx.AsyncClient], params: dict[str, Any]
) -> httpx.Response | None:
    """广告库 API 请求。双通道：代理优先（国内直连 Meta 域被墙），失败切直连（海外部署）。"""
    for client in clients:
        try:
            return await client.get(_ADS_URL, params=params)
        except httpx.HTTPError:
            continue
    return None


async def _fetch_page(
    clients: tuple[httpx.AsyncClient, httpx.AsyncClient], url: str
) -> str | None:
    """抓 FB 主页 HTML。双通道：代理优先，连接失败 / 代理软拦截（202）换直连再试。"""
    for client in clients:
        try:
            resp = await client.get(url)
        except httpx.HTTPError:
            continue
        # 202 = 代理对目标站软拦截（website_enrich 踩过的坑），换通道再试
        if resp.status_code == 200 and len(resp.text) > 500:
            return resp.text
    return None


class MetaAdsCollector(Collector):
    name = "meta_ads"
    title = "Meta 广告库（出海投放挖掘）"
    param_schema = [
        {
            "key": "keywords",
            "label": "搜索关键词",
            "required": True,
            "type": "tags",
            "placeholder": "行业/品类词，如 smart watch, leggings, wig",
            "default": "",
        },
        {
            "key": "countries",
            "label": "投放目标市场",
            "required": True,
            "type": "multiselect",
            "options": COUNTRY_OPTIONS,
            "placeholder": "广告投放的国家（WA 高渗透市场优先）",
            "default": "",
        },
        {
            "key": "max_pages",
            "label": "翻页数",
            "required": False,
            "type": "number",
            "placeholder": "每关键词最多翻几页（每页 100 条广告）",
            "default": "2",
        },
        {
            "key": "probe_pages",
            "label": "探测广告主主页",
            "required": False,
            "type": "switch",
            "placeholder": "抓每个主页提取 WhatsApp/邮箱/官网（慢但信息全）",
            "default": "true",
        },
    ]

    def validate_params(self, params: dict[str, Any]) -> None:
        require_params(params, "keywords", "countries", collector=self.title)

    async def run(self, ctx: TaskContext) -> None:
        if not settings.META_ADS_ACCESS_TOKEN:
            raise BusinessError(
                code=40001,
                message="未配置 META_ADS_ACCESS_TOKEN：在 backend/.env 加上后重启。"
                "免费申请：https://www.facebook.com/ads/archive/api（只需 ads_archive 只读权限）",
            )

        keywords = split_csv(str(ctx.params.get("keywords")))
        countries = [c.upper() for c in split_csv(str(ctx.params.get("countries")))]
        try:
            max_pages = max(1, int(str(ctx.params.get("max_pages") or "2")))
        except ValueError:
            max_pages = 2
        probe = str(ctx.params.get("probe_pages", "true")).lower() != "false"
        ctx.set_total(len(keywords))
        await ctx.log(
            "info",
            f"广告库搜索：{len(keywords)} 个关键词 × 投放国 {','.join(countries)}，"
            f"翻页≤{max_pages}，主页探测{'开' if probe else '关'}",
        )

        # page_id 去重（一次运行里同一广告主多个广告只处理一次）
        seen_pages: dict[str, dict[str, Any]] = {}
        query_ok = 0
        headers = {"User-Agent": _UA}

        # 双通道：代理优先（国内网络 Meta 域直连被墙，实测直连 000），直连兜底（海外部署）
        async with (
            httpx.AsyncClient(headers=headers, timeout=_TIMEOUT) as via_proxy,
            httpx.AsyncClient(headers=headers, timeout=_TIMEOUT, trust_env=False) as direct,
        ):
            clients = (via_proxy, direct)
            for kw in keywords:
                ctx.check_cancelled()
                await ctx.log("info", f"搜索广告：「{kw}」")
                after: str | None = None
                kw_ads = 0
                for _page in range(max_pages):
                    params: dict[str, Any] = {
                        "access_token": settings.META_ADS_ACCESS_TOKEN,
                        "search_terms": kw,
                        "ad_reached_countries": ",".join(countries),
                        "ad_type": "ALL",
                        "ad_active_status": "ACTIVE",
                        "fields": _ADS_FIELDS,
                        "limit": _PAGE_SIZE,
                    }
                    if after:
                        params["after"] = after
                    resp = await _ads_get(clients, params)
                    if resp is None:
                        await ctx.log("error", f"「{kw}」请求失败：代理/直连两条通道都不通（检查网络或代理）")
                        break
                    if resp.status_code != 200:
                        # token 无效/过期是常见配置问题，给可直接行动的提示
                        hint = "（token 无效或过期，去 Ads Library API 重新生成）" if '"code":190' in resp.text else ""
                        await ctx.log("error", f"「{kw}」API {resp.status_code}{hint}：{resp.text[:200]}")
                        break
                    data = resp.json()
                    for ad in data.get("data", []):
                        pid = str(ad.get("page_id") or "")
                        if not pid:
                            continue
                        kw_ads += 1
                        rec = seen_pages.setdefault(pid, {
                            "page_name": ad.get("page_name") or "",
                            "page_profile_uri": ad.get("page_profile_uri") or "",
                            "bodies": [],
                            "ad_count": 0,
                            "countries": [],
                        })
                        rec["ad_count"] += 1
                        # 累计投放国（ad_reached_countries 是 ISO2 列表）
                        for c in ad.get("ad_reached_countries") or []:
                            cu = str(c).upper()
                            if len(cu) == 2 and cu not in rec["countries"]:
                                rec["countries"].append(cu)
                        body = (ad.get("ad_creative_bodies") or [""])[0] or ""
                        if body and len(rec["bodies"]) < 3 and body not in rec["bodies"]:
                            rec["bodies"].append(body)
                        if ad.get("page_name") and not rec["page_name"]:
                            rec["page_name"] = ad["page_name"]
                        if ad.get("page_profile_uri") and not rec["page_profile_uri"]:
                            rec["page_profile_uri"] = ad["page_profile_uri"]
                    paging = (data.get("paging") or {}).get("cursors") or {}
                    after = paging.get("after")
                    if not after or not data.get("data"):
                        break
                    await asyncio.sleep(_API_DELAY)
                if kw_ads:
                    query_ok += 1
                    await ctx.log("info", f"「{kw}」命中 {kw_ads} 条在投广告，涉及 {len(seen_pages)} 个广告主（累计）")
                ctx.inc_progress(1)

            if query_ok == 0:
                raise BusinessError(code=40001, message="全部关键词查询失败，任务判 failed（不产出空结果假成功）")

            await ctx.log("info", f"去重后共 {len(seen_pages)} 个广告主，开始产出线索" + ("并探测主页" if probe else ""))

            # 第二阶段：产出 + 主页探测。多国投放时 country 记第一个（列是单值）
            primary_country = countries[0] if countries else None
            if len(countries) > 1:
                await ctx.log("info", f"多国投放，线索 country 统一记为 {primary_country}（投放国明细看任务日志）")
            probe_fail = 0
            skipped_non_company = 0
            for pid, rec in seen_pages.items():
                ctx.check_cancelled()
                name = rec["page_name"] or f"FB主页 {pid}"
                profile_uri = rec["page_profile_uri"] or f"https://www.facebook.com/profile.php?id={pid}"
                draft = LeadDraft(
                    source="meta_ads",
                    name=name,
                    country=primary_country,
                    social={"facebook": profile_uri},
                    # 出海画像：该广告主实际投放的国家全量（country 只是第一个投放国）
                    target_countries=sorted(rec.get("countries") or []) or list(countries),
                    # 广告信号（§4.1）：本次搜索命中的在投广告数（合并语义取 max）
                    ad_count=int(rec.get("ad_count") or 0),
                )

                if probe and rec["page_profile_uri"]:
                    html = await _fetch_page(clients, rec["page_profile_uri"])
                    if html is None:
                        probe_fail += 1
                        await ctx.log("warn", f"主页抓取失败：{name}")
                    else:
                        # 只保留公司/企业：按主页类目过滤（公众人物/自媒体/媒体/政府…跳过）
                        category = _extract_category(html)
                        if not _is_company_category(category):
                            skipped_non_company += 1
                            await ctx.log(
                                "info", f"跳过非企业主页：{name}（类目：{category or '未知'}）"
                            )
                            continue
                        if category:
                            draft.industry = _FB_CATEGORY_ZH.get(
                                category.lower().strip(), category.strip()
                            )
                        wa_url = detect_whatsapp([html])[1]
                        if wa_url:
                            draft.whatsapp_url = wa_url
                            draft.fb_whatsapp = True  # 主页挂 WA 按钮 = 私域运营证据
                            # 号码证据链（§4.1）：主页出现的全部 WA 号码
                            draft.whatsapp_numbers = detect_whatsapp_numbers([html])
                            phone = _extract_wa_phone(html)
                            if phone:
                                # wa.me 号码是完整国际号（无 +）：补 + 强制按国际解析，
                                # 否则会被 draft.country（投放国）当成本地号解析失败
                                draft.phone_raw = f"+{phone}"
                        email = detect_email(html)
                        if email:
                            draft.email = email
                        site = _extract_site(html)
                        if site:
                            draft.website = site
                        await asyncio.sleep(_PROBE_DELAY)
                # 中文特征：品牌名 / 广告文案任一含 CJK
                draft.is_cn = _looks_cn([rec["page_name"], *rec["bodies"]])

                wa_tag = "，带 WA 私域按钮" if draft.fb_whatsapp else ""
                await ctx.log(
                    "info",
                    f"线索：{name}{wa_tag}（在投广告 {rec['ad_count']} 条）"
                    + (f" wa={draft.phone_raw}" if draft.phone_raw else "")
                    + (f" email={draft.email}" if draft.email else ""),
                )
                lead_id, _created = await ctx.emit(draft)

                # 信号级证据链（§4.1）：广告在投 / FB WA 按钮 / 主页号码
                from app.crud.lead_signals import upsert_signal
                from app.db.session import async_session

                async with async_session() as session:
                    await upsert_signal(
                        session, lead_id, "meta_ad",
                        f"{rec['ad_count']} 条在投（{','.join(sorted(rec.get('countries') or []))}）",
                        source="meta_ads", evidence_url=profile_uri,
                        evidence_raw=(rec.get("bodies") or [""])[0][:200] or None,
                        confidence=95,
                    )
                    if draft.fb_whatsapp:
                        await upsert_signal(
                            session, lead_id, "fb_whatsapp", draft.phone_raw or "button",
                            source="meta_ads", evidence_url=profile_uri,
                            evidence_raw=draft.whatsapp_url, confidence=90,
                        )
                        for n in draft.whatsapp_numbers or []:
                            await upsert_signal(
                                session, lead_id, "whatsapp_number", n,
                                source="meta_ads", evidence_url=profile_uri,
                                evidence_raw=f"https://wa.me/{n}", confidence=90,
                            )
                    await session.commit()

            if probe and probe_fail:
                await ctx.log("warn", f"主页探测失败 {probe_fail} 个（登录墙/网络），线索已按无探测信息落库")
            await ctx.log(
                "info",
                f"完成：{len(seen_pages)} 个广告主，跳过非企业 {skipped_non_company} 个 → "
                f"{len(seen_pages) - skipped_non_company} 条线索已入库（去重合并自动处理）",
            )
