"""b2b_supplier 采集器：中国制造网（made-in-china.com）出口供应商目录发现。

漏斗方向（2026-09-01 用户裁决：搜索/招聘发现的目标不相关，meta_ads 无 token
暂不可用）：出口工厂本来就注册在 B2B 目录里等海外买家找到——按品类词去目录
领名单，相关性与量都是现成的：

    品类搜索页 products-search/hot-china-products/{品类}.html（单页 ~15 家店铺）
    → 店铺子域 {slug}.en.made-in-china.com
        首页 h1 = 公司英文名（如 Xuchang Yuanxiu Crafts Co., Ltd）
    → 公司档案页 company-{Name}.html，公开字段（实测全公开，无需登录）：
        Main Markets（出口市场清单）/ Main Products / Address / Incoterms

联系方式不在平台页（登录墙是 B2B 平台商业模式）——线索落库后由自动接力走
「官网发现（公司名搜官网）→ 官网富化」从公司自己的官网联系页拿（富化检测层
12 类形态，真实站点验证过）。B2B 挂单进 overseas_signals（出海证据），
ICP 走 country=CN + is_cn 强证据。

robots 纪律（2026-09-01 核对 robots.txt）：/company-search/ 与 /multi-search/
被 Disallow 不使用；本采集器只走 products-search 目录页与店铺子域页面。
礼貌：页面间隔 ≥3s、每轮供应商上限默认 30、单 httpx 直连（国内可达）。
"""

from __future__ import annotations

import asyncio
import html as _html
import re
import urllib.parse
from typing import Any

import httpx

from app.collectors.base import Collector, LeadDraft, TaskContext, split_csv
from app.core.exceptions import BusinessError

_MIC_BASE = "https://www.made-in-china.com"
_PAGE_GAP = 3.0  # 品类页之间的礼貌间隔（秒）
_SUP_GAP = 3.0  # 店铺/档案页之间的礼貌间隔（秒）
_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

DEFAULT_KEYWORDS = "wig,LED strip,pet products,outdoor furniture,hair extension"

# 品类页/店铺页里的供应商店铺子域（协议相对与绝对两种形态都有）
_SUP_SUB_RE = re.compile(r"(?:https:)?//([a-z0-9-]{2,})\.en\.made-in-china\.com")
# 店铺首页里的档案页链接（company-{Name}.html）
_PROFILE_HREF_RE = re.compile(r'href="((?:https://[a-z0-9-]+\.en\.made-in-china\.com)?/company-[^"]+\.html)"')
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)

# 档案页「官网」字段候选标签（部分供应商会填自己的官网；实测多数为空）
_WEBSITE_LABELS = ("Web Site", "Website", "Company Website", "Homepage")
_HREF_RE = re.compile(r'href="(https?://[^"]+)"')


def _clean_text(fragment: str) -> str:
    """HTML 片段 → 纯文本：剥标签、反转义、去 Unicode bidi 控制符、折叠空白。"""
    txt = _html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    txt = re.sub(r"[‪-‮⁦-⁩]", "", txt)
    return re.sub(r"\s+", " ", txt).strip()


def parse_search_page(html: str) -> list[str]:
    """品类搜索页 → 供应商店铺子域 slug 列表（保序去重，排除 www 等平台自身域）。"""
    out: list[str] = []
    for slug in _SUP_SUB_RE.findall(html or ""):
        if slug in ("www", "m") or slug in out:
            continue
        out.append(slug)
    return out


def parse_supplier_root(html: str) -> dict[str, str | None]:
    """店铺首页 → {name, profile_path}。

    公司名优先 h1（最干净），回退 title 末段「… Supplier - {Company}」。
    档案页链接是相对/绝对两种形态，统一返回绝对可拼的路径。
    """
    h1 = _H1_RE.search(html or "")
    name = _clean_text(h1.group(1)) if h1 else ""
    if not name:
        t = re.search(r"<title>(.*?)</title>", html or "", re.S)
        if t:
            title = _clean_text(t.group(1))
            name = title.rsplit(" - ", 1)[-1].strip() if " - " in title else ""
    prof = _PROFILE_HREF_RE.search(html or "")
    return {
        "name": name or None,
        "profile_path": prof.group(1) if prof else None,
    }


def parse_company_profile(html: str) -> dict[str, Any]:
    """公司档案页 → {main_products, address, markets, incoterms, website}。

    档案信息是 info-item 结构（实测，tests/fixtures/mic_company_profile.html）：
        <div class="info-item"><div class="info-label">Main Markets:</div>
        <div class="info-fields">North America, …</div></div>
    兼容 th/td 表格行兜底。值全公开；「官网」字段多数供应商不填，
    能拿就带回去（省一次官网发现搜索）。
    """
    rows: list[tuple[str, str]] = []  # (标签, 值片段原文)
    for block in re.split(r'class="info-item"', html or "")[1:]:
        lab_m = re.search(r'class="info-label"[^>]*>(.*?)</div>', block, re.S)
        val_m = re.search(r'class="info-fields"[^>]*>(.*?)</div>', block, re.S)
        if lab_m and val_m:
            label = _clean_text(lab_m.group(1)).rstrip(":").strip()
            if label:
                rows.append((label, val_m.group(1)))
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html or "", re.S):
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)
        if len(cells) >= 2:
            label = _clean_text(cells[0]).rstrip(":").strip()
            if label and label not in [r[0] for r in rows]:
                rows.append((label, cells[1]))

    def field(*labels: str) -> str:
        for lab in labels:
            for got, raw in rows:
                if got.lower() == lab.lower():
                    return _clean_text(raw)
        return ""

    markets = [m.strip() for m in field("Main Markets").split(",") if m.strip()]
    products = [m.strip() for m in field("Main Products", "Main Product").split(",") if m.strip()]
    incoterms = [m.strip() for m in field("International Commercial Terms(Incoterms)", "Incoterms").split(",") if m.strip()]
    # 官网字段：值片段里取第一个外链（排除平台自身域；「Verify Now」认证
    # 外链不在官网标签行里，不会误入）
    website = None
    for lab in _WEBSITE_LABELS:
        for got, raw in rows:
            if got.lower() == lab.lower():
                for href in _HREF_RE.findall(raw):
                    if "made-in-china" not in href and "micstatic" not in href:
                        website = href
                        break
            if website:
                break
        if website:
            break
    return {
        "main_products": products,
        "address": field("Address")[:200] or None,
        "markets": markets,
        "incoterms": incoterms[:6],
        "website": website,
    }


async def _fetch(client: httpx.AsyncClient, url: str) -> str | None:
    """GET → HTML；失败/非 200 返回 None（调用方记日志继续，不放大为任务失败）。"""
    try:
        r = await client.get(url)
    except httpx.HTTPError:
        return None
    return r.text if r.status_code == 200 else None


def build_draft(*, name: str, keyword: str, profile: dict[str, Any]) -> LeadDraft:
    """店铺信息 → LeadDraft（CN 强证据 + B2B 出口挂单出海证据）。"""
    signals: dict[str, list[str]] = {"b2b_export": [f"中国制造网出口挂单（{keyword}）"]}
    if profile.get("incoterms"):
        signals["b2b_export"].append(f"Incoterms: {', '.join(profile['incoterms'])}")
    if profile.get("markets"):
        # Main Markets 与富化官网检测的 markets 键同源（合并语义 union）
        signals["markets"] = list(profile["markets"])
    if profile.get("main_products"):
        signals["b2b_export"].append(f"Main Products: {', '.join(profile['main_products'][:5])}")
    industry = keyword
    if profile.get("main_products"):
        industry = f"{keyword}·{profile['main_products'][0]}"[:60]
    return LeadDraft(
        source="b2b_supplier",
        name=name,
        country="CN",
        is_cn=True,
        industry=industry,
        address=profile.get("address"),
        website=profile.get("website"),
        overseas_signals=signals,
        social={},
    )


class B2BSupplierCollector(Collector):
    name = "b2b_supplier"
    title = "B2B 出口目录（中国制造网）"
    logic_note = (
        "【抓什么】按品类词到中国制造网（made-in-china.com）领出口供应商名单："
        "挂在 B2B 目录上的都是等着被海外买家找到的中国工厂/贸易商——"
        "「中国企业 × 出海」两个资格条件天然成立，不再从搜索结果里大海捞针。\n"
        "【怎么抓】品类搜索页 → 供应商店铺 → 公司档案页（公司名/出口市场/"
        "主营产品/Incoterms 全公开）。店铺联系方式在平台是登录墙——线索入库后"
        "系统自动接力「官网发现 + 官网富化」，从公司自己的官网联系页提取"
        "电话/邮箱/WhatsApp。\n"
        "【词怎么填】品类词（wig、LED strip、假发、宠物用品），越具体越准；"
        "英文词结果更全，中文也可用。每轮最多处理 30 家（礼貌限速），"
        "多配几个品类词比配一个宽词产出高。\n"
        "【准确性】出口市场清单（Main Markets）是一手资料，直接进出海证据；"
        "同一家公司重复出现会按域名/电话/公司名三身份列合并成一条线索。"
        "平台页不提供联系方式，联系信息以官网富化结果为准（每条都可点开证据核对）。\n"
        "【建议节奏】配成每周 1-2 次定时跑换不同品类词，配合每天的全库富化，"
        "新线索会自动补全官网与联系方式。"
    )
    param_schema = [
        {
            "key": "keywords",
            "label": "品类关键词",
            "required": False,
            "type": "tags",
            # 站点是英文站：英文品类词的搜索结果显著更全（实测 wig vs 假发），
            # 中文词也可用但结果少；默认词覆盖跨境热门品类
            "placeholder": "英文品类词结果更全（wig、LED strip、pet products、outdoor furniture），中文也可用（假发、宠物用品）；越具体越准",
            "default": DEFAULT_KEYWORDS,
        },
        {
            "key": "max_suppliers",
            "label": "每轮最多处理供应商数",
            "required": False,
            "type": "number",
            "placeholder": "默认 30（礼貌限速：每家店铺 2 次抓取、间隔 3 秒）；多品类词轮转处理，先保证覆盖面",
            "default": "30",
        },
    ]

    async def run(self, ctx: TaskContext) -> None:
        keywords = split_csv(str(ctx.params.get("keywords"))) or split_csv(DEFAULT_KEYWORDS)
        try:
            budget = max(5, min(int(ctx.params.get("max_suppliers") or 30), 100))
        except ValueError:
            budget = 30

        async with httpx.AsyncClient(
            headers={"User-Agent": _UA, "Accept-Language": "en,zh-CN;q=0.9"},
            timeout=_TIMEOUT,
            trust_env=False,  # 国内直连（代理策略：不捡环境变量）
            follow_redirects=True,
        ) as client:
            # ---------- 阶段一：品类页收集店铺子域 ----------
            by_kw: dict[str, list[str]] = {}
            ok_pages = 0
            for kw in keywords:
                ctx.check_cancelled()
                url = f"{_MIC_BASE}/products-search/hot-china-products/{urllib.parse.quote(kw)}.html"
                page = await _fetch(client, url)
                if page is None:
                    await ctx.log("warn", f"品类页抓取失败：「{kw}」（稍后重跑或换词）")
                else:
                    ok_pages += 1
                    by_kw[kw] = parse_search_page(page)
                    await ctx.log("info", f"「{kw}」→ {len(by_kw[kw])} 家供应商店铺")
                await asyncio.sleep(_PAGE_GAP)
            if ok_pages == 0:
                raise BusinessError(
                    code=50001,
                    message="全部品类页抓取失败（中国制造网限流或网络异常）——稍后重跑或降低品类词数",
                )

            # ---------- 阶段二：轮转处理店铺（多词均衡，不集中在第一个词） ----------
            queue: list[tuple[str, str]] = []
            if by_kw:
                for i in range(max(len(v) for v in by_kw.values())):
                    for kw in keywords:
                        slugs = by_kw.get(kw) or []
                        if i < len(slugs):
                            queue.append((kw, slugs[i]))
            queue = queue[:budget]
            ctx.set_total(len(queue))

            created = merged = failed = 0
            web_direct = 0  # 档案页直接带官网字段的（免一次官网发现搜索）
            for idx, (kw, slug) in enumerate(queue):
                ctx.check_cancelled()
                if idx:
                    await asyncio.sleep(_SUP_GAP)
                root_url = f"https://{slug}.en.made-in-china.com/"
                root_html = await _fetch(client, root_url)
                if root_html is None:
                    failed += 1
                    await ctx.log("warn", f"店铺首页抓取失败：{slug}（跳过）")
                    ctx.inc_progress(1)
                    continue
                info = parse_supplier_root(root_html)
                if not info["name"]:
                    failed += 1
                    await ctx.log("warn", f"店铺首页无公司名：{slug}（跳过）")
                    ctx.inc_progress(1)
                    continue

                profile: dict[str, Any] = {}
                profile_url = None
                if info["profile_path"]:
                    path = info["profile_path"]
                    profile_url = path if path.startswith("http") else urllib.parse.urljoin(root_url, path)
                    await asyncio.sleep(_SUP_GAP)
                    prof_html = await _fetch(client, profile_url)
                    if prof_html is not None:
                        profile = parse_company_profile(prof_html)
                    else:
                        await ctx.log("warn", f"档案页抓取失败：{slug}（只带店铺信息入库）")

                draft = build_draft(name=info["name"], keyword=kw, profile=profile)
                if draft.website:
                    web_direct += 1
                lead_id, is_new = await ctx.emit(draft)
                if is_new:
                    created += 1
                else:
                    merged += 1
                # 出海证据（出口市场一手清单）进证据链，可点开档案页核对
                if lead_id:
                    markets = profile.get("markets") or []
                    await self._write_signal(
                        ctx,
                        lead_id,
                        content=(
                            f"B2B 出口平台挂单（中国制造网·{kw}）"
                            + (f"，出口市场：{'、'.join(markets[:6])}" if markets else "")
                        ),
                        evidence_url=profile_url or root_url,
                    )
                ctx.inc_progress(1)

            head = f"B2B 目录采集完成：新增 {created}、合并 {merged}、失败 {failed}"
            if web_direct:
                head += f"；档案页直接带官网 {web_direct} 家"
            head += "——联系方式由自动接力富化从公司官网补全"
            await ctx.log("info", head)

    @staticmethod
    async def _write_signal(ctx: TaskContext, lead_id: int, *, content: str, evidence_url: str | None) -> None:
        """出海证据写信号链（失败降级 warn，不放大为任务失败）。"""
        from app.crud.lead_signals import upsert_signal
        from app.db.session import async_session

        try:
            async with async_session() as session:
                await upsert_signal(
                    session,
                    lead_id,
                    "b2b_signal",
                    content,
                    source="b2b_supplier",
                    evidence_url=evidence_url,
                    confidence=75,
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            await ctx.log(
                "warn",
                f"信号证据写库失败（忽略）：{type(exc).__name__}: {str(exc)[:60]}",
            )
