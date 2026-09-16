"""web_search 采集器：搜索引擎 → 企业种子发现（默认引擎零 key 零费用）。

PRD §6.2 P1 数据源「Google/Bing 搜索」+ §二「企业发现引擎（搜索）」：
按关键词搜公开网页（典型用法："whatsapp customer service" 跨境电商、
supplier "chat on whatsapp"），从结果标题+URL 提取企业官网种子，
source=web_search 入库（去重合并自动处理），后续靠 website_enrich 富化信号。

引擎（SEARCH_ENGINE，默认 duckduckgo——纯开源免费部署零配置零 key）：
- duckduckgo  html.duckduckgo.com HTML 端点，无 key 无费用（默认）
- searxng     自托管开源元搜索引擎（SEARXNG_URL，format=json）——生产推荐，
              聚合 Google/Bing/DDG 结果且不被单家限流
- google_cse  Google Custom Search JSON API（免费 100 次/天，超出付费——可选加速）
- bing        Bing Web Search API（付费——可选加速）

合规：默认引擎为公开网页端点/自托管实例；官方付费 API 仅作可选加速通道。
"""

from __future__ import annotations

import asyncio
import re
import urllib.parse
from typing import Any

import httpx

from app.collectors.base import Collector, LeadDraft, TaskContext, require_params, split_csv
from app.collectors.icp import NON_BUYER_DOMAINS as _NON_BUYER_DOMAINS
from app.collectors.normalize import extract_domain
from app.core.config import settings
from app.core.exceptions import BusinessError

_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
# 买家黑名单单源（2026-09-01 巡检裁决：两处人工双写已实锤漏改——letschuhai/
# iciba 都是「只补一边」后复现）：icp.NON_BUYER_DOMAINS 是唯一真源（存量兜底），
# 这里整包叠加做入库前拦截，增补只改 icp.py 一处。下面另加的是搜索引擎结果
# 特有的平台/社媒/门户域（不是买家门语义）
_NON_SITE_DOMAINS = tuple(
    dict.fromkeys(
        (
            "facebook.com",
            "instagram.com",
            "linkedin.com",
            "tiktok.com",
            "youtube.com",
            "twitter.com",
            "x.com",
            "amazon.",
            "shopee.",
            "lazada.",
            "alibaba.com",
            "aliexpress.com",
            "ebay.",
            "wikipedia.org",
            "reddit.com",
            "quora.com",
            "medium.com",
            "github.com",
            "google.com",
            "microsoft.com",
            "apple.com",
            "zapier.com",
            "hubspot.com",
            "salesforce.com",
            "zendesk.com",
            "whatsapp.com",
            "wa.me",
            "blogspot.",
            "wordpress.com",
            "wixsite.com",
            "duckduckgo.com",
            "searx",
            "bing.com",
            "baidu.com",
            # 中国门户/媒体/内容社区（实测 sohu.com 被当线索入库）
            "sohu.com",
            "sina.com",
            "163.com",
            "qq.com",
            "zhihu.com",
            "weibo.com",
            "jd.com",
            "tmall.com",
            "taobao.com",
            "toutiao.com",
            "36kr.com",
            "csdn.net",
            "bilibili.com",
            "douyin.com",
            "xiaohongshu.com",
            "ifeng.com",
            "cnblogs.com",
            "jianshu.com",
            "oschina.net",
            "gitee.com",
            # 平台补漏（2026-08-31 审计）：微信/ Etsy / Temu / B2B 平台 / Pinterest 等
            "wechat.com",
            "etsy.com",
            "temu.com",
            "1688.com",
            "made-in-china.com",
            "globalsources.com",
            "pinterest.com",
            "t.me",
            "threads.net",
            "discord.com",
            # 买家门同源黑名单（gov.cn/edu.cn TLD、行业媒体、词典站、错配宿主域等）
            *_NON_BUYER_DOMAINS,
        )
    )
)


def _is_blocked_domain(domain: str) -> bool:
    """平台/社媒域判定（域边界锚定，2026-08-31 审计修复）。

    旧实现两处误杀：endswith 未加 "." 前缀（"x.com" 杀 netflix.com、
    "jd.com" 杀 3jd.com）；裸子串 `d in domain`（"ebay." 杀 bluebay.com）。
    带尾点的条目（"amazon."）本意是「任意 TLD 的区域站」（amazon.de/
    shopee.sg）——归一为裸标签按前缀匹配。与 meta_ads 的判定统一锚定域边界。
    """
    d = domain.lower()
    # WhatsApp 软件/镜像站域族（2026-09-01 实测：pwa-whatsapp.com 等「WhatsApp
    # 网页版」镜像站轮换域名霸榜搜索结果）——中国出海企业官网域名不含该词，
    # 子串规则防枚举漏网
    if "whatsapp" in d:
        return True
    for entry in _NON_SITE_DOMAINS:
        b = entry.rstrip(".").lower()
        if d == b or d.endswith("." + b):
            return True
        # 裸标签（原带尾点，如 amazon./ebay./shopee./lazada./blogspot./searx）：
        # 匹配「标签.任意TLD」与「子域.标签.任意TLD」（amazon.de / shop.baidu.gg）
        if "." not in b and (d.startswith(b + ".") or d.endswith("." + b + ".")):
            return True
    return False


# DDG HTML 结果：标题链接与跳转参数
_DDGLINK_RE = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DDGTAG_RE = re.compile(r"<[^>]+>")
_DDGUDDG_RE = re.compile(r"[?&]uddg=([^&]+)")
# 下一页链接（class="result__pagination"）里的 s= 偏移——单页 ~10-30 条不满
# max_results 时按它翻页（2026-08-31：单页只有 ~10 条，20 条配额形同虚设）
_DDGNEXT_RE = re.compile(r'class="result__pagination"[^>]*href="([^"]+)"')
_DDG_S_RE = re.compile(r"[?&]s=(\d+)")


# 文章/内容页启发词（标题或 URL 路径命中即弃——搜索「whatsapp 客服」类词
# 首页结果大量是内容平台文章，不是要找的企业官网）
_ARTICLE_TITLE_WORDS = (
    "指南",
    "测评",
    "手册",
    "必看",
    "攻略",
    "如何",
    "怎么办",
    "完整",
    "清单",
    "排行",
    "对比",
    "top 10",
    "top10",
    "best ",
    "how to",
    "guide",
    "review",
    "tutorial",
    "vs ",
    "tips",
    "checklist",
    "解密",
    "深度",
    "解析",
    "入门",
    "实战",
    # 内容门户/知识站（2026-08-31 实测漏网：「外贸知识大全-外贸知识网」混进线索池）
    "大全",
    "知识网",
    "百科",
    "资讯网",
    "论坛",
    "问答",
    "导航",
    # 词典/翻译/释义页（2026-09-01 实测漏网：「制造是什么意思_制造的翻译_音标_读音_
    # 用法_例句_爱词霸」整条当线索入库——品类词搜索结果页全是这类页，不是企业官网）
    "是什么",
    "什么意思",
    "翻译",
    "音标",
    "读音",
    "例句",
    "词典",
    "辞典",
    "释义",
    "词霸",
    "definition",
    "meaning",
    "translation",
    "pronunciation",
    # 问句/教程形态（2026-09-01 实测漏网：「外贸私域体系怎么搭建，从客户触达到
    # 长期转化的…」文章标题整条入库——「怎么办」拦不住「怎么搭建」）
    "怎么",
    "一文",
    "教程",
    "干货",
    "盘点",
    # 词典站英文形态（2026-09-01 实测漏网：「NONE Synonyms: 83 Similar and
    # Opposite Words」thesaurus.com 词条整条入库）
    "synonym",
    "antonym",
    "thesaurus",
    "opposite words",
    # 教程/FAQ 形态（2026-09-01 清库重测漏网：「常见 LED 功能和 LED 驱动器
    # 设计注意事项」TI 官方技术文章以 qualified 混入销售池）
    "注意事项",
    "常见问题",
)
_ARTICLE_PATH_WORDS = (
    "/blog",
    "/article",
    "/articles",
    "/news",
    "/docs",
    "/doc/",
    "/archives",
    "/zh_cn/",
    "/zh-cn/",
    "/support/",
    "/help/",
    "/learn/",
    "/resources",
    "/post/",
    "/dy/article",
    "/p/",
    ".html",
    ".htm",
    ".php",
)
# 聚合内容页分隔符（爱词霸式「词_翻译_音标_例句」堆叠栏标题）；
# 不含 "-"——合法品牌名带连字符常见（Coca-Cola），误杀面不可接受
_AGGREGATE_SEPARATORS = ("_", "|", "｜", "·")


def _looks_like_article(title: str, url: str) -> bool:
    """标题/URL 是否内容页（文章/文档/词典/聚合页）而非企业官网页面。"""
    t = title.lower()
    if any(w in t for w in _ARTICLE_TITLE_WORDS):
        return True
    # 标题堆 ≥2 个聚合分隔符 = 词典/列表页标题形态，不是公司名
    if sum(t.count(sep) for sep in _AGGREGATE_SEPARATORS) >= 2:
        return True
    path = urllib.parse.urlparse(url).path.lower()
    return any(w in path for w in _ARTICLE_PATH_WORDS)


def _is_company_site(url: str) -> bool:
    """搜索结果 URL 是否企业官网（滤平台/社媒/文档站）。

    匹配必须锚定域边界（2026-08-31 审计修复）：旧实现 endswith 未加 "." 前缀，
    "x.com" 会误杀 netflix.com/dropbox.com、"jd.com" 误杀 3jd.com；裸子串
    `d in domain` 同样误杀（bluebay.com 含 "ebay."）。与 meta_ads 的
    `host == d or host.endswith("." + d)` 统一。
    """
    domain = (extract_domain(url) or "").lower()
    if not domain:
        return False
    return not _is_blocked_domain(domain)


def _ddg_unwrap(href: str) -> str:
    """DDG 跳转链接（//duckduckgo.com/l/?uddg=<urlencode>）→ 真实 URL。"""
    m = _DDGUDDG_RE.search(href)
    if m:
        return urllib.parse.unquote(m.group(1))
    if href.startswith("//"):
        return "https:" + href
    return href


def parse_ddg_html(html: str) -> list[dict[str, Any]]:
    """DDG HTML 结果页 → [{title, url}]（归一形态，喂 results_to_drafts）。"""
    items: list[dict[str, Any]] = []
    for m in _DDGLINK_RE.finditer(html or ""):
        url = _ddg_unwrap(m.group(1))
        title = _DDGTAG_RE.sub("", m.group(2)).strip()
        if url and title:
            items.append({"title": title, "url": url})
    return items


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


# ---------- 必应中国版（bing_cn）：国内直连可达的兜底引擎 ----------
# DDG 国内直连被墙、走代理又常被出口 IP 限流（202 实测）；cn.bing.com 直连
# 可用但对「频繁新建 TLS 连接」敏感——必须 keep-alive 复用 + 秒级间隔 + 一次重试。
_BING_H2_RE = re.compile(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_BING_TAG_RE = re.compile(r"<[^>]+>")


def parse_bing_html(html: str) -> list[dict[str, Any]]:
    """cn.bing.com 结果页 → [{title, url}]（b_algo 结果块的 h2 直链，无跳转包装）。"""
    items: list[dict[str, Any]] = []
    for block in re.split(r'class="b_algo"', html or "")[1:]:
        m = _BING_H2_RE.search(block)
        if not m:
            continue
        url = m.group(1).strip()
        title = _BING_TAG_RE.sub("", m.group(2)).strip()
        if url.startswith(("http://", "https://")) and title:
            items.append({"title": title, "url": url})
    return items


async def _search_bing(
    clients: tuple[httpx.AsyncClient, httpx.AsyncClient], kw: str, limit: int
) -> tuple[list[dict[str, Any]], str | None]:
    """cn.bing.com 直连搜索（直连优先，代理兜底；连接异常退避 8s 重试一次）。

    翻页取满 limit（first=1/11/21…，≤4 页，页间 2s）。2026-08-31 用户反馈
    「爬取量太少」：此前只取第一页 ≈10 条且 count 参数对 HTML 端点不生效，
    max_results>10 形同虚设——必应 HTML 单页固定 ~10 条 b_algo，翻页是唯一增量。
    """
    err: str | None = None
    for client in (clients[1], clients[0]):
        collected: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        first = 1
        for _page in range(4):
            resp: httpx.Response | None = None
            for attempt in range(2):
                try:
                    resp = await client.get(
                        "https://cn.bing.com/search",
                        params={"q": kw, "first": first},
                        headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"},
                        follow_redirects=True,
                    )
                    break
                except httpx.HTTPError as exc:
                    # 连续新建连接会被掐 TLS（惩罚窗口约 1 分钟）——退避 8s 重试一次
                    err = f"必应连接失败 {type(exc).__name__}"
                    if attempt == 0:
                        await asyncio.sleep(8)
            if resp is None or resp.status_code != 200:
                if resp is not None:
                    err = f"必应 {resp.status_code}"
                break
            new = [x for x in parse_bing_html(resp.text) if x["url"] not in seen_urls]
            if not new:
                break
            collected.extend(new)
            seen_urls.update(x["url"] for x in new)
            if len(collected) >= limit:
                break
            first += 10
            await asyncio.sleep(2)
        if collected:
            return collected[:limit], None
    return [], err or "必应不可达"


def drafts_with_stats(
    items: list[dict[str, Any]], params_is_cn: Any = True
) -> tuple[list[LeadDraft], dict[str, int]]:
    """搜索结果项 → (企业官网种子 Draft 列表, 滤因统计)。

    滤因统计供任务日志展示（「10 条结果 → 滤 6 平台域 / 2 内容页 → 2 种子」），
    操作者能看懂为什么产出少，而不是以为逻辑坏了。

    is_cn 语义（2026-08-31 巡检修 ICP 门完整性）：**参数开 ∧ 标题含中文**才算
    CN 证据。此前参数开着就盲标——搜英文词（如 whatsapp/whatapps 拼错）时
    meta.com、gizmodo.com 这类纯海外站也被标成「中国企业」，富化出海外信号
    后会以 qualified 混进中国出海销售池，击穿 ICP 硬门。收紧后：中文标题
    （中文关键词搜索的自然结果）→ 中国企业；英文标题 → is_cn=False 留给
    富化判定（ICP 备案号 / 官网中文 ≥30%），unknown 不做有罪推定。
    """
    drafts: list[LeadDraft] = []
    stats = {"platform_domain": 0, "article_page": 0, "dup_domain": 0}
    seen_domains: set[str] = set()
    is_cn_param = (
        str(params_is_cn).lower() != "false"
        if isinstance(params_is_cn, str)
        else bool(params_is_cn)
    )
    for it in items:
        url = (it.get("url") or it.get("link") or "").strip()
        title = (it.get("name") or it.get("title") or "").strip()
        if not url or not title:
            continue
        if not _is_company_site(url):
            stats["platform_domain"] += 1
            continue
        if _looks_like_article(title, url):
            stats["article_page"] += 1  # 内容页（指南/测评/博客）不是企业官网种子
            continue
        domain = extract_domain(url) or ""
        if not domain or domain in seen_domains:
            stats["dup_domain"] += 1
            continue
        seen_domains.add(domain)
        # 种子入口归一为站点根：富化从首页开始，内页由链接发现逻辑自己找
        url = f"https://{domain}"
        for sep in (" - ", " | ", " – ", " — "):
            if sep in title:
                title = title.split(sep)[0].strip() or title
                break
        # 泛标题（「首页/Home」类）拿不到公司名，入库就是垃圾行
        # （2026-09-01 实测：gy2025.com 以 name=「首页」入库；同批还实测到
        # 字面 "None"/"none"/「NONE Synonyms…」入库——搜索引擎对空名结果
        # 的兜底标题，同样过滤）
        plain_title = title.strip().strip('"\'“”')
        if plain_title.lower() in (
            "首页", "home", "index", "main", "主页",
            "none", "null", "undefined", "nan", "undefined synonyms",
        ):
            stats["generic_title"] = stats.get("generic_title", 0) + 1
            continue
        d = LeadDraft(source="web_search", name=title[:255], website=url)
        d.is_cn = is_cn_param and _has_cjk(title)
        drafts.append(d)
    return drafts, stats


def results_to_drafts(items: list[dict[str, Any]], params_is_cn: Any = True) -> list[LeadDraft]:
    """drafts_with_stats 的列表形态（官网发现等只取 website 的调用方用）。"""
    return drafts_with_stats(items, params_is_cn)[0]


async def search_with_fallback(
    clients: tuple[httpx.AsyncClient, httpx.AsyncClient],
    kw: str,
    limit: int,
    log=None,
) -> tuple[list[dict[str, Any]], str | None, str]:
    """主引擎搜索，DDG 不可达自动切必应。返回 (items, err, 实际使用的引擎)。

    供本采集器与 website_enrich 的官网发现共用——单引擎故障不中断任何链路。
    """
    engine = settings.SEARCH_ENGINE
    items, err = await _search(clients, engine, kw, limit)
    used = engine
    if err and engine == "duckduckgo":
        if log is not None:
            await log("warn", f"「{kw}」{err}，自动切换必应（cn.bing.com 直连）重试")
        items, err = await _search_bing(clients, kw, limit)
        if not err:
            used = "bing_cn"
    return items, err, used


async def _search(
    clients: tuple[httpx.AsyncClient, httpx.AsyncClient], engine: str, kw: str, limit: int
) -> tuple[list[dict[str, Any]], str | None]:
    """按引擎执行搜索，返回 (归一结果, 错误信息)。

    双通道（与 meta_ads 同策略）：代理优先（国内网络访问 DDG/Google 域被墙），
    连接失败/软拦截切直连兜底（海外部署）。
    """
    if engine == "duckduckgo":
        err: str | None = None
        for client in clients:
            collected: list[dict[str, Any]] = []
            seen_urls: set[str] = set()
            offset = 0
            # 单页 ~10-30 条；翻页取满 limit（≤4 页，礼貌间隔 0.5s，无下一页即停）
            for _page in range(4):
                try:
                    resp = await client.post(
                        "https://html.duckduckgo.com/html/",
                        data={"q": kw, "kl": "wt-wt", "s": offset},
                        headers={"User-Agent": _UA, "Referer": "https://duckduckgo.com/"},
                    )
                except httpx.HTTPError as exc:
                    err = f"DDG 连接失败 {type(exc).__name__}"
                    break
                if resp.status_code != 200:
                    err = f"DDG {resp.status_code}"
                    break
                err = None
                new = [x for x in parse_ddg_html(resp.text) if x["url"] not in seen_urls]
                if not new:
                    break
                collected.extend(new)
                seen_urls.update(x["url"] for x in new)
                if len(collected) >= limit:
                    break
                m = _DDGNEXT_RE.search(resp.text)
                next_s: int | None = None
                if m:
                    sm = _DDG_S_RE.search(m.group(1))
                    next_s = int(sm.group(1)) if sm else None
                if next_s is None or next_s <= offset:
                    break
                offset = next_s
                await asyncio.sleep(0.5)
            if collected:
                return collected[:limit], None
            if err:
                continue
        return [], err or "DDG 不可达"

    if engine == "searxng":
        if not settings.SEARXNG_URL:
            return [], "未配置 SEARXNG_URL"
        resp = await clients[0].get(
            f"{settings.SEARXNG_URL.rstrip('/')}/search",
            params={"q": kw, "format": "json"},
        )
        if resp.status_code != 200:
            return [], f"SearxNG {resp.status_code}"
        return resp.json().get("results", [])[:limit], None

    if engine == "google_cse":
        if not (settings.GOOGLE_CSE_KEY and settings.GOOGLE_CSE_CX):
            return [], "未配置 GOOGLE_CSE_KEY/CX"
        resp = await clients[0].get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": settings.GOOGLE_CSE_KEY,
                "cx": settings.GOOGLE_CSE_CX,
                "q": kw,
                "num": min(limit, 10),  # CSE 单页上限 10
            },
        )
        if resp.status_code != 200:
            return [], f"Google CSE {resp.status_code}"
        return resp.json().get("items", [])[:limit], None

    if engine == "bing":
        if not settings.BING_SEARCH_KEY:
            return [], "未配置 BING_SEARCH_KEY"
        resp = await clients[0].get(
            "https://api.bing.microsoft.com/v7.0/search",
            headers={"Ocp-Apim-Subscription-Key": settings.BING_SEARCH_KEY},
            params={"q": kw, "count": limit, "responseFilter": "Webpages"},
        )
        if resp.status_code != 200:
            return [], f"Bing {resp.status_code}"
        return (resp.json().get("webPages") or {}).get("value", [])[:limit], None

    if engine == "bing_cn":
        return await _search_bing(clients, kw, limit)

    return [], f"未知引擎：{engine}"


class WebSearchCollector(Collector):
    name = "web_search"
    title = "搜索引擎发现（DuckDuckGo / SearxNG）"
    logic_note = (
        "【抓什么】用搜索引擎按关键词找企业官网，作为线索种子入库。联系方式和购买"
        "意向不在这步判断——由下一步「网站富化」抓官网核实。\n"
        "【引擎与降级】默认 DuckDuckGo（免费无 key，国内需代理）；DuckDuckGo 连不上时"
        "自动切换必应中国版（cn.bing.com 直连，无需代理）重试，单引擎故障不会导致任务失败。\n"
        "【过滤规则】① 电商平台和社交媒体的链接（facebook/amazon/alibaba 等平台、社媒与门户域黑名单）不算企业官网；"
        "② 文章页（指南、测评、博客）丢弃；③ 同一网站只留一条，入口统一为网站首页。\n"
        "【准确性】只在「结果标题含中文」时才标记为中国企业；英文标题的网站保持待验证，"
        "等富化抓到 ICP 备案号或中文内容后再认定——避免把海外公司误当中国出海企业。\n"
        "【自动接力】任务完成后系统自动执行「网站富化」（找官网、抓信号、重新评分），"
        "已有全库富化在排队时不会重复堆任务。\n"
        "【关键词怎么填】三轴词形：① WA-first 量词形（\"wa.me\" 86 品类）——搜到页面=中国号码\n"
        "WhatsApp 链接 = WA 信号+CN 证据一条命中，直接产出 S/A；② 品类×出口身份词（假发 厂家 外贸、\n"
        "宠物用品 供应商 出口）——找工厂官网本体；③ 独立站词形（户外家具 独立站）。\n"
        "泛行业词（跨境电商 客服）与单词（whatsapp）搜到的全是文章/词典/下载页。\n"
        "【通道定位（2026-09-01 实测）】国内直连下是**弱种子辅助通道**：单词产出 0-5 个种子\n"
        "且混媒体站——扩量主力是 B2B 出口目录（b2b_supplier）与招聘站品类词发现（job_posting\n"
        "开『发现新线索』+ 品类词），WA-first 量词形则把本通道升级为直接产出 S/A 的补强。\n"
        "【边界】搜索只负责发现候选，是否值得跟进由 ICP 准入和评分决定。"
    )
    param_schema = [
        {
            "key": "keywords",
            "label": "搜索关键词",
            "required": True,
            "type": "tags",
            # 三轴词形（2026-09-01 定稿）：WA-first 量词形（直接命中已在用
            # WhatsApp 的中国企业 = S/A 制造机）> 品类×出口身份词（工厂官网）>
            # 独立站词形。泛行业词（跨境电商 客服）与单词（whatsapp）搜到的
            # 全是文章/词典/下载页，必被过滤
            "placeholder": "WA-first 量词形（\"wa.me\" 86 假发、\"api.whatsapp.com/send\" 86 LED灯带）最值钱：搜到页面=中国号码 WhatsApp 链接=WA 信号+CN 证据一条命中；品类×出口身份词（假发 厂家 外贸）做补充。泛行业词（跨境电商 客服）搜到的全是文章/社区/门户，必被过滤",
            "default": (
                '"wa.me" 86 假发,"api.whatsapp.com/send" 86 LED灯带,'
                "户外家具 厂家 外贸,宠物用品 供应商 出口"
            ),
        },
        {
            "key": "max_results",
            "label": "每词最多取多少条结果",
            "required": False,
            "type": "number",
            "placeholder": "1-50",
            "default": "20",
        },
        {
            "key": "is_cn",
            "label": "标记为中国企业（出海 ICP）",
            "required": False,
            "type": "switch",
            "placeholder": "主爬中国企业（默认开）；关掉则采集海外本地企业",
            "default": "true",
        },
    ]

    def validate_params(self, params: dict[str, Any]) -> None:
        require_params(params, "keywords", collector=self.title)
        engine = settings.SEARCH_ENGINE
        if engine == "searxng" and not settings.SEARXNG_URL:
            raise BusinessError(
                code=40001, message="SEARCH_ENGINE=searxng 需在 .env 配 SEARXNG_URL"
            )
        if engine == "google_cse" and not (settings.GOOGLE_CSE_KEY and settings.GOOGLE_CSE_CX):
            raise BusinessError(
                code=40001, message="SEARCH_ENGINE=google_cse 需配 GOOGLE_CSE_KEY/CX"
            )
        if engine == "bing" and not settings.BING_SEARCH_KEY:
            raise BusinessError(code=40001, message="SEARCH_ENGINE=bing 需配 BING_SEARCH_KEY")

    async def run(self, ctx: TaskContext) -> None:
        raw_kw = ctx.params.get("keywords")
        keywords = split_csv(raw_kw) if isinstance(raw_kw, str) and raw_kw.strip() else []
        try:
            max_results = max(1, min(int(ctx.params.get("max_results") or 20), 50))
        except ValueError:
            max_results = 20
        engine = settings.SEARCH_ENGINE
        # DDG 撞墙记忆：本网络 DDG 不可达时，后续关键词直接走必应——
        # 否则每个词都白等一次 DDG 超时（实测 ~7s/词，纯浪费且刷屏 warn）
        ddg_dead = False
        ctx.set_total(len(keywords))
        ok_queries = 0

        async with (
            # 采集代理策略（2026-09-01 用户裁决）：环境变量代理已在 config 启动时
            # 剥离；境外端点（DDG）只在显式配置 CRAWLER_PROXY_URL 时走代理，否则
            # 直连（海外部署可用）——必应中国无论何时都直连（clients[1] 优先）
            httpx.AsyncClient(
                timeout=_TIMEOUT,
                trust_env=False,
                proxy=settings.CRAWLER_PROXY_URL or None,
            ) as via_proxy,
            httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as direct,
        ):
            clients = (via_proxy, direct)
            for kw in keywords:
                ctx.check_cancelled()
                await ctx.log("info", f"搜索（{'bing_cn' if ddg_dead else engine}）：「{kw}」")
                if ddg_dead:
                    items, err = await _search_bing(clients, kw, max_results)
                    engine_used = "bing_cn"
                else:
                    # 主引擎 + DDG 不可达自动切必应（search_with_fallback 与官网发现共用）
                    items, err, engine_used = await search_with_fallback(
                        clients, kw, max_results, log=ctx.log
                    )
                    if engine_used == "bing_cn":
                        ddg_dead = True
                        await ctx.log(
                            "info", "DDG 本轮不可达，后续关键词直接走必应（省去每词超时等待）"
                        )
                if err:
                    await ctx.log("error", f"「{kw}」搜索失败：{err}")
                else:
                    ok_queries += 1
                    drafts, fstats = drafts_with_stats(items, ctx.params.get("is_cn", True))
                    for d in drafts:
                        await ctx.emit(d)
                    if items:
                        # 滤因透明化：产出少时操作者能看到是平台域/内容页/同域重复/泛标题滤掉的
                        cn_n = sum(1 for d in drafts if d.is_cn)
                        await ctx.log(
                            "info",
                            f"「{kw}」{len(items)} 条结果 → {len(drafts)} 个企业官网种子"
                            f"（滤 平台/社媒域 {fstats['platform_domain']} · 内容页 {fstats['article_page']}"
                            f" · 同域重复 {fstats['dup_domain']}"
                            f" · 泛标题 {fstats.get('generic_title', 0)}；标中国企业 {cn_n}）",
                        )
                ctx.inc_progress(1)
                # 引擎礼貌间隔：必应对频繁新建连接敏感，间隔拉长
                await asyncio.sleep(4.0 if engine_used == "bing_cn" else 1.0)

        if keywords and ok_queries == 0:
            raise BusinessError(
                code=50001,
                message=f"全部关键词搜索失败（引擎 {engine}，检查网络/凭据/实例可达性）",
            )
