"""cifnews_cases 采集器：雨果跨境（cifnews.com）跨境名人堂采访报道。

数据源（2026-09-16 调研）：cifnews.com/topic/426「跨境名人堂」每篇是一段对
中国出海公司创始人的深度采访——含公司名、销量数据、目标市场、品类。

为什么有 ICP 价值：雨果的采访对象默认都是中国出海企业（已具备规模、有付费
SaaS 能力）——精准命中 BSP（出海 SaaS）目标画像。每篇文章公开报道，零反爬。

抓取模式：
    列表页 topic/426 → /article/{id} 单篇 → 启发式提取公司名
    → is_cn=True（中国公司强证据）
    → industry 从正文/标题关键词推断（毛绒玩具/3D打印/E-Bike 等）
    → 自动接力 website_enrich 找官网 + 补电话/邮箱

准确率预估：
    公司名提取 ~70-85%（启发式「XX 公司」/「以下简称「XX」」）
    行业推断 ~60%（关键词匹配）
    联系方式靠 website_enrich 接力（已知 lead 41% 命中）
"""

from __future__ import annotations

import asyncio
import html as _html
import re
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.collectors.base import Collector, LeadDraft, TaskContext
from app.core.config import settings

_BASE = "https://www.cifnews.com"
_TOPIC_URL = f"{_BASE}/topic/426"  # 跨境名人堂
_TOPIC_GAP = 3.0
_ARTICLE_GAP = 2.0
_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# 列表页里的文章详情链接
_ARTICLE_HREF_RE = re.compile(r'href="(/article/(\d+))"')
# 公司名提取（按优先级）
# 1. 以下简称「X」/ （X）— 简称模式，最准
_SHORT_NAME_RE = re.compile(r"以下简称[「『]([^」』]{2,30})[」』]")
# 2. "XX是一家..." / "XX 成立于..." — 全称
_COMPANY_INTRO_RE = re.compile(
    r"([\u4e00-\u9fff（）()·•\sA-Za-z0-9]{2,40}"
    r"(?:科技|有限|实业|集团|股份|电子|商务|贸易|智能|网络|信息|文化|品牌|互娱|互娱|控股)"
    r"公司)"
)
# 3. 标题里的「X出海/品牌/集团/科技」词
_TITLE_COMPANY_RE = re.compile(
    r"^([^：，,。\s《》【】()（）]{2,20}(?:科技|集团|品牌|股份|实业|电子))"
)

# 行业关键词 → 推断 industry
_INDUSTRY_KEYWORDS = {
    "毛绒玩具": "毛绒玩具",
    "玩具": "玩具",
    "宠物": "宠物用品",
    "3D 打印": "3D 打印设备",
    "3D打印": "3D 打印设备",
    "E-Bike": "电动自行车",
    "电动自行车": "电动自行车",
    "电动滑板": "电动滑板",
    "骑行": "骑行装备",
    "健身": "健身器材",
    "户外": "户外装备",
    "智能首饰": "智能首饰",
    "情趣": "情趣用品",
    "宠物纪念": "宠物用品",
    "贴纸": "文具/办公",
    "AI": "AI 应用",
}


def _clean_text(fragment: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_topic_page(html: str) -> list[str]:
    """列表页 → article id 列表（保序去重）。"""
    out: list[str] = []
    seen: set[str] = set()
    for _href, aid in _ARTICLE_HREF_RE.findall(html or ""):
        if aid in seen:
            continue
        seen.add(aid)
        out.append(aid)
    return out


def parse_article(html: str) -> dict[str, Any]:
    """单篇 article HTML → {title, name, industry_hint}。

    提取策略（按准确度优先级）：
    1. 抓 <h1> 标题（最干净）
    2. 抓正文里「以下简称「X」」的 X（最强证据 = 该文主角公司）
    3. 抓「XX 是一家」「XX 成立于」的 XX 公司全称
    4. fallback：用标题里的品牌名 + 类目

    返回字段：
    - title: 文章标题
    - name: 公司名（首选简称）
    - industry_hint: 行业关键词命中项
    """
    title_m = re.search(r"<h1[^>]*>(.*?)</h1>", html or "", re.S)
    title = _clean_text(title_m.group(1)) if title_m else ""

    # 正文 HTML 全部 strip tags 后看纯文本（公司名通常在首段）
    plain = _clean_text(re.sub(r"<[^>]+>", " ", html or ""))

    name = ""
    # 1. 「以下简称「X」」— 最优先
    sn = _SHORT_NAME_RE.search(plain)
    if sn:
        name = sn.group(1).strip()
    # 2. 全称匹配
    if not name:
        cn = _COMPANY_INTRO_RE.search(plain)
        if cn:
            name = cn.group(1).strip()
    # 3. fallback: 标题里的品牌词
    if not name:
        tn = _TITLE_COMPANY_RE.search(title)
        if tn:
            name = tn.group(1).strip()

    # 行业关键词命中
    industry = None
    for kw, ind in _INDUSTRY_KEYWORDS.items():
        if kw in plain or kw in title:
            industry = ind
            break

    return {
        "title": title,
        "name": name,
        "industry_hint": industry,
    }


class CifnewsCasesCollector(Collector):
    name = "cifnews_cases"
    title = "雨果跨境采访报道（中国出海公司深度报道）"
    logic_note = (
        "【抓什么】cifnews.com/topic/426「跨境名人堂」每篇是对中国出海公司创始人的"
        "深度采访——含公司名、销量数据、目标市场、品类。\n"
        "【怎么抓】列表页 topic/426 解析所有 /article/{id} → 单篇 article HTML "
        "启发式提取公司名（首选「以下简称「X」」简称模式，fallback 全称/标题品牌词）。"
        "联系方式不在文章里，留给 website_enrich 从公司自己的官网联系页拿。\n"
        "【词怎么填】无关键词参数：雨果采访是人工策展，每篇都是高质量 BSP 候选。\n"
        "【准确性】雨果采访对象默认中国出海公司 = 高质量线索；公司名启发式提取"
        "准确率预估 70-85%。\n"
        "【建议节奏】每周 1 次定时跑（雨果每周更新 5-10 篇采访）。\n"
        "【边界】纯营销页（HTML 直出，无登录墙），2-3s 礼貌间隔防压测。\n"
        "【提示⚠️】雨果采访报道会有数字（年入 3 亿等）混在标题中，"
        "提取公司名时会被启发式误截取——以「以下简称「X」」和全称模式为优先。\n"
        "【限制⚠️ 2026-09-16 实测】topic/426 列表页**当前不可用**：\n"
        "  - HTML 静态页：article 链接数 = 0（JS 渲染失败）\n"
        "  - Playwright headless 渲染 + 滚动懒加载：仍 0 article 链接\n"
        "  - 内部 API api.cifnews.com/page/ajax/important?key=theme_detail&theme_id=426 返回 data=[]\n"
        "  - DDG/Bing 搜索引擎搜不到具体 /article/xxx URL\n"
        "**单篇 /article/{id} HTML 可抓**（实测成功），但没有稳定的列表源。\n"
        "绕过方案（未来任一可行时再注册到 _REGISTRY）：\n"
        "  1. 雨果官网改版后让 topic/426 的 article 链接在 HTML 里出\n"
        "  2. 列表 API 改用 key=theme_article_list 或类似公开字段\n"
        "  3. 用雨果首页文章列表（每篇也含 company info）+ 关键词过滤「采访/对话」\n"
        "  4. 用第三方聚合站（Notion/Reddit RSS 等）做 article id 来源\n"
    )
    param_schema = [
        {
            "key": "max_articles",
            "label": "每轮最多处理文章数",
            "required": False,
            "type": "number",
            "placeholder": "默认 20（雨果采访每周新增 5-10 篇，预算留足）",
            "default": "20",
        },
        {
            "key": "min_id",
            "label": "起始 article id",
            "required": False,
            "type": "number",
            "placeholder": "默认 0（处理最新）。设上次处理的 id 跳过旧文章，避免重复",
            "default": "0",
        },
    ]

    async def run(self, ctx: TaskContext) -> None:
        try:
            budget = max(1, min(int(ctx.params.get("max_articles") or 20), 50))
        except ValueError:
            budget = 20
        try:
            min_id = int(ctx.params.get("min_id") or 0)
        except ValueError:
            min_id = 0

        # 双 client：直连 + 代理兜底（雨果国内直连通常通，但为防 CF 兜底）
        proxy_url = settings.CRAWLER_PROXY_URL or ""

        @asynccontextmanager
        async def _noop():
            yield None

        proxy_cm = (
            httpx.AsyncClient(
                headers={"User-Agent": _UA, "Accept-Language": "zh-CN;q=0.9"},
                timeout=_TIMEOUT,
                trust_env=False,
                follow_redirects=True,
                proxy=proxy_url,
            )
            if proxy_url
            else _noop()
        )
        async with httpx.AsyncClient(
            headers={"User-Agent": _UA, "Accept-Language": "zh-CN;q=0.9"},
            timeout=_TIMEOUT,
            trust_env=False,
            follow_redirects=True,
        ) as direct, proxy_cm as proxy:
            # ---------- 阶段一：列表页收集 article id ----------
            ctx.check_cancelled()
            list_html = await _fetch(direct, _TOPIC_URL)
            if list_html is None and proxy is not None:
                await ctx.log(
                    "info",
                    f"列表页直连失败，自动切代理 {proxy_url} 重试",
                )
                list_html = await _fetch(proxy, _TOPIC_URL)
            if list_html is None:
                raise CifnewsError(
                    code=50001,
                    message=f"列表页抓取失败：{_TOPIC_URL}",
                )
            article_ids = parse_topic_page(list_html)
            article_ids = [aid for aid in article_ids if int(aid) > min_id][:budget]
            ctx.set_total(len(article_ids))
            await ctx.log("info", f"列表页 → {len(article_ids)} 篇新采访（min_id={min_id}）")

            created = merged = skipped = 0
            for aid in article_ids:
                ctx.check_cancelled()
                url = f"{_BASE}/article/{aid}"
                html = await _fetch(direct, url)
                if html is None and proxy is not None:
                    html = await _fetch(proxy, url)
                if html is None:
                    skipped += 1
                    await ctx.log("warn", f"文章 #{aid} 抓取失败（跳过）")
                    ctx.inc_progress(1)
                    continue
                info = parse_article(html)
                if not info["name"] or len(info["name"]) < 3:
                    skipped += 1
                    await ctx.log(
                        "warn",
                        f"文章 #{aid} 提取不出公司名（跳过）：{info['title'][:50]}",
                    )
                    ctx.inc_progress(1)
                    continue
                draft = build_draft(info, aid)
                lead_id, is_created = await ctx.emit(draft)
                if is_created:
                    created += 1
                    await ctx.log(
                        "info",
                        f"✅ #{aid} → {info['name']}（新建 #{lead_id}，行业={info['industry_hint'] or '未知'}）",
                    )
                else:
                    merged += 1
                    await ctx.log(
                        "info",
                        f"↪️ #{aid} → {info['name']}（合并到 #{lead_id}）",
                    )
                ctx.inc_progress(1)
                await asyncio.sleep(_ARTICLE_GAP)

            await ctx.log(
                "info",
                f"任务完成：{len(article_ids)} 篇 → 新建 {created}、合并 {merged}、跳过 {skipped}",
            )


async def _fetch(client: httpx.AsyncClient | None, url: str) -> str | None:
    if client is None:
        return None
    try:
        r = await client.get(url)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    return r.text


def build_draft(info: dict[str, Any], article_id: str) -> LeadDraft:
    """文章 → LeadDraft。

    source = cifnews:case:{article_id}（按来源去重区分）
    is_cn = True（雨果采访对象默认中国公司）
    industry 来自启发式关键词命中；fallback 留空
    """
    return LeadDraft(
        source=f"cifnews:case:{article_id}",
        name=info["name"][:255],
        is_cn=True,
        industry=info.get("industry_hint"),
    )


class CifnewsError(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(message)