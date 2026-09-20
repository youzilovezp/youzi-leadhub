"""shopline_stories 采集器：SHOPLINE 公开成功案例页（success-stories/<slug>）。

数据源（2026-09-16 调研）：shopline.com/success-stories 列表页展示真实出海品牌
（Karada/NomadsFi/Seattle Gummy/Pricklee …）。每个 `/success-stories/<slug>`
详情页含：
    - 公司名（slug 化的标题）
    - 创始人/CEO 名 + 引语
    - 主营业务（如 wellness studio、subscription 电商）
    - SHOPLINE 商家 logo + 详情文本

为什么有 ICP 价值：SHOPLINE 服务中国出海独立站，入选「成功故事」意味着该商家
- 是中国团队运营（is_cn 证据）
- 已实现海外订阅/会员/规模化（出海证据）
- 正在用 SHOPLINE（platform 信号，可推荐替代或配套 SaaS）
这些商家即「已出海 + 在用建站 SaaS」的目标客户画像——精准命中 BSP（出海 SaaS）。

联系方式不在案例页——线索落库后由自动接力走「官网发现 → 官网富化」
从公司自己的官网联系页拿（同 b2b_supplier 模式）。

robots 纪律：SHOPLINE 营销页无 robots 限制（public marketing），礼貌间隔 3s。
"""

from __future__ import annotations

import asyncio
import html as _html
import re
from typing import Any

import httpx

from app.collectors.base import Collector, LeadDraft, TaskContext
from app.core.config import settings

_BASE = "https://www.shopline.com"
_LIST_URL = f"{_BASE}/success-stories"
_PAGE_GAP = 3.0
_DETAIL_GAP = 3.0
_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# 列表页里的详情链接：/success-stories/<slug>
_STORY_HREF_RE = re.compile(r'href="(/success-stories/([a-z0-9-]+))"')
# 详情页里的公司名（h1 标签）
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
# 详情页里的「CEO/创始人」引语（"<name>, <role>" 形态）
# 例：Evelyn Ji, Head of Global Growth  /  Tamson Tan, CEO
_CEO_RE = re.compile(
    r'"([^"]+?),'
    r'\s*(?:CEO|Head of|Head|Co-?Founder|Founder|Director|Manager)'
    r'[^"<]{1,80}',
    re.S,
)


def _clean_text(fragment: str) -> str:
    """HTML 片段 → 纯文本：剥标签、反转义、折叠空白。"""
    txt = _html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    return re.sub(r"\s+", " ", txt).strip()


def parse_list_page(html: str) -> list[str]:
    """列表页 → 案例 slug 列表（保序去重，排除当前页与已页外的非案例锚）。"""
    out: list[str] = []
    for _href, slug in _STORY_HREF_RE.findall(html or ""):
        # 排除锚链接与分页（虽然这里没看到分页，先防一手）
        if slug in out or slug in ("success-stories",):
            continue
        out.append(slug)
    return out


def parse_story_page(html: str, slug: str) -> dict[str, Any]:
    """详情页 → {name, website?, ceo?, industry_hint?}。

    name 取 h1 文本（最干净）；fallback 从 <title> 末尾的「... | SHOPLINE」取前缀。
    website 与 ceo 暂不直接抽取（案例页通常只展示 logo + 引语，公司自己的
    官网留给 website_enrich 接力补全；CEO 引语解析的形态太多，泛化易误报）。
    行业线索从 slug + 公司名推断（如「fashion」「pet」「beauty」直接进 industry）。
    """
    h1 = _H1_RE.search(html or "")
    name = _clean_text(h1.group(1)) if h1 else ""
    if not name:
        t = re.search(r"<title>(.*?)</title>", html or "", re.S)
        if t:
            title = _clean_text(t.group(1))
            # 形态：「Karada | SHOPLINE」或「Shopline x Karada」
            for sep in (" | SHOPLINE", " - SHOPLINE", " – SHOPLINE"):
                if sep in title:
                    name = title.split(sep)[0].strip()
                    break
            if not name and title:
                name = title.split(" | ")[0].strip()
    name = name or slug.replace("-", " ").title()
    return {
        "name": name,
        "slug": slug,
    }


def _industry_from_slug(slug: str) -> str | None:
    """slug 含行业词的简单推断（miss = None，留给 career_site / scenes 跑出来）。"""
    table = {
        "karada": "wellness",
        "nomadsfi": "wifi",
        "seattle-gummy": "supplement",
        "pricklee": "beverage",
    }
    return table.get(slug)


class ShoplineStoriesCollector(Collector):
    name = "shopline_stories"
    title = "SHOPLINE 公开成功案例（中国出海独立站）"
    logic_note = (
        "【抓什么】shopline.com/success-stories 列表页 → 每个案例详情页。"
        "每个商家都是「已出海 + 在用建站 SaaS」的目标客户——精准命中 BSP 画像。\n"
        "【怎么抓】列表页解析 `/success-stories/<slug>` 锚链接；详情页取 h1 公司名；"
        "CEO 引语 + 联系方式不在案例页，留给 website_enrich 从公司自己的官网"
        "联系页拿（与 b2b_supplier 同模式）。\n"
        "【词怎么填】无关键词参数：列表页是固定的 ~4 个精选案例（SHOPLINE 营销页"
        "人工策展），按 slug 全量处理最简；如需扩量请走 web_search 找未上榜商家。\n"
        "【准确性】入选「成功故事」= SHOPLINE 官方背书 = 高质量线索；去重依赖"
        "upsert_lead 的 domain/phone/公司名+城市 三身份列自动处理。\n"
        "【建议节奏】每周 1 次定时跑（新案例每周更新，频率不高）。\n"
        "【边界】纯营销页（前台公开、无登录墙），3s 礼貌间隔防压测。\n"
        "【限制⚠️】2026-09-16 实测：shopline.com 在 Cloudflare v3 Managed "
        "Challenge 后——直连 + 代理 + headless Chromium 三种方式都返回 403「Just a "
        "moment...」挑战页，需真实浏览器才能过。当前实现：直连失败 → 代理兜底 → 都失败则"
        "任务失败并提示。要走通需：(1) 用户本机浏览器提前开列表页拿到 cf_clearance "
        "cookie 注入；或 (2) 走 SHOPLINE 国内镜像 shoplineapp.cn（如有类似页）；"
        "或 (3) 改走 web_search 搜 `site:shopline.com success story`。"
    )
    param_schema = [
        {
            "key": "max_stories",
            "label": "每轮最多处理案例数",
            "required": False,
            "type": "number",
            "placeholder": "默认 20（SHOPLINE 列表页当前精选 ~4 个，留余地防增量）",
            "default": "20",
        },
    ]

    async def run(self, ctx: TaskContext) -> None:
        try:
            budget = max(1, min(int(ctx.params.get("max_stories") or 20), 50))
        except ValueError:
            budget = 20

        # 双 client：直连 + 代理兜底（shopline.com 在 Cloudflare 后，国内直连常被 403 挑战）
        proxy_url = settings.CRAWLER_PROXY_URL or ""
        async with httpx.AsyncClient(
            headers={"User-Agent": _UA, "Accept-Language": "en,zh-CN;q=0.9"},
            timeout=_TIMEOUT,
            trust_env=False,
            follow_redirects=True,
        ) as direct, _proxy_client_or_none(proxy_url) as proxy:
            # ---------- 阶段一：列表页收集案例 slug ----------
            ctx.check_cancelled()
            list_html = await _fetch(direct, _LIST_URL)
            if list_html is None and proxy is not None:
                await ctx.log(
                    "info",
                    f"列表页直连被 Cloudflare 挑战，自动切代理 {proxy_url} 重试",
                )
                list_html = await _fetch(proxy, _LIST_URL)
            if list_html is None:
                raise BusinessError_shopline(
                    code=50001,
                    message=f"列表页抓取失败：{_LIST_URL}（直连 + 代理都失败——"
                    f"请检查 CRAWLER_PROXY_URL 配置或网络可达性）",
                )
            slugs = parse_list_page(list_html)
            slugs = slugs[:budget]
            ctx.set_total(len(slugs))
            await ctx.log("info", f"列表页 → {len(slugs)} 个案例 slug")

            created = merged = failed = 0
            for slug in slugs:
                ctx.check_cancelled()
                url = f"{_BASE}/success-stories/{slug}"
                html = await _fetch(direct, url)
                if html is None and proxy is not None:
                    html = await _fetch(proxy, url)
                if html is None:
                    failed += 1
                    await ctx.log("warn", f"案例页抓取失败：{slug}（跳过）")
                    ctx.inc_progress(1)
                    continue
                info = parse_story_page(html, slug)
                draft = build_draft(slug=slug, name=info["name"], html=html)
                if not draft:
                    failed += 1
                    await ctx.log("warn", f"案例页无公司名：{slug}（跳过）")
                    ctx.inc_progress(1)
                    continue
                lead_id, is_created = await ctx.emit(draft)
                if is_created:
                    created += 1
                    await ctx.log(
                        "info",
                        f"✅ {slug} → {info['name']}（新建 #{lead_id}）",
                    )
                else:
                    merged += 1
                    await ctx.log(
                        "info",
                        f"↪️ {slug} → {info['name']}（合并到 #{lead_id}）",
                    )
                ctx.inc_progress(1)
                await asyncio.sleep(_DETAIL_GAP)

            await ctx.log(
                "info",
                f"任务完成：{len(slugs)} 个案例 → 新建 {created}、 合并 {merged}、失败 {failed}",
            )


async def _fetch(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        r = await client.get(url)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    return r.text


def _proxy_client_or_none(proxy_url: str):
    """无代理 URL 时返 None，让 async with 跳过；有则返 client。"""
    if not proxy_url:
        # 返一个无操作的 async context manager（避免 async with 内部为 None 报错）
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _noop():
            yield None

        return _noop()
    return httpx.AsyncClient(
        headers={"User-Agent": _UA, "Accept-Language": "en,zh-CN;q=0.9"},
        timeout=_TIMEOUT,
        trust_env=False,
        follow_redirects=True,
        proxy=proxy_url,
    )


def build_draft(*, slug: str, name: str, html: str) -> LeadDraft | None:
    """案例页 → LeadDraft。

    source = shopline_stories（去重时按源区分）
    is_cn = True（SHOPLINE 服务中国出海商家，平台强证据）
    country 暂不写——案例页未声明目标国，留给 website_enrich 从公司官网 + overseas
    信号推断。
    industry 从 slug 推断；fallback 留空。
    """
    if not name or len(name) < 2:
        return None
    industry = _industry_from_slug(slug)
    return LeadDraft(
        source=f"shopline_stories:{slug}",
        name=name[:255],
        is_cn=True,
        industry=industry,
    )


# 故意继承最简 Exception：避免 core.BusinessError 误把 SHOPLINE 错误码归到
# 自家通用池——本采集器自定义错码更易追踪
class BusinessError_shopline(Exception):  # noqa: N801  命名沿用 core 模块风格
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(message)