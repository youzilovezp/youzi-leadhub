"""出海业务信号检测（PRD §4.2）：官网页 → 出海证据。

六类信号（键 → 中文标签）：
- currencies：页面出现海外货币（USD/EUR/GBP/AED/SAR…代码或符号）
- languages：多语言版本（hreflang / 语言切换链接 / /en / /de 路径）
- ecommerce：电商建站栈指纹（Shopify/WooCommerce/Amazon/TikTok Shop…）
- shipping：海外配送表达（worldwide/international shipping + 国家列表）
- markets：页面提及的海外市场国家名（USA/UK/UAE/Saudi/Brazil…）
- export_words：出海自述（global supplier/export to/international buyer…）

输入是 HTML 列表（首页+联系页+产品页），输出 {键: [证据串]}——命中项
去重保序，直接可落 JSON 列与 lead_signals 证据表。
"""

from __future__ import annotations

import re
from typing import Any

# ---------- 货币（代码 / 符号） ----------
# 命中的证据串取「代码」，符号只做辅助确认（$ 太常见，单符号不算证据）。
# 符号分两类处理边界：
# - 字母符号（RM/QR/KD/BD/OMR）：前加 \b，防 "confirm 150" 里的 rm 误命中
# - 非字母符号（$ € £ ¥ …）：\b 对其永不成立（前后都是非单词字符），不加
_CURRENCY_TABLE = (
    ("USD", "$"), ("EUR", "€"), ("GBP", "£"), ("AED", "د.إ"),
    ("SAR", "﷼"), ("QAR", "QR"), ("KWD", "KD"), ("BHD", "BD"),
    ("OMR", "OMR"), ("MYR", "RM"), ("SGD", "S$"), ("JPY", "¥"),
    ("AUD", "A$"), ("CAD", "C$"),
)


def _currency_pattern(code: str, sym: str) -> re.Pattern[str]:
    sym_prefix = r"\b" if sym.isascii() and sym.isalnum() else ""
    return re.compile(
        rf"\b{code}\b|{sym_prefix}{re.escape(sym)}\s?\d|[\d.,]+\s?{re.escape(sym)}",
        re.IGNORECASE,
    )


_CURRENCY_RES: list[tuple[str, re.Pattern[str]]] = [
    (code, _currency_pattern(code, sym)) for code, sym in _CURRENCY_TABLE
]

# ---------- 多语言版本 ----------
_HREFLANG_RE = re.compile(r'hreflang=["\']([a-zA-Z]{2}(?:-[a-zA-Z]{2})?)["\']', re.I)
_LANG_SWITCH_RE = re.compile(
    r'href=["\'][^"\']*(?:/(en|de|fr|es|pt|it|nl|ru|ar|ja|ko|th|vi|id|ms)(?:/[^"\']*)?)["\']',
    re.I,
)

# ---------- 电商建站栈 ----------
# (平台, 正则)：域名/CDN/注释指纹
_ECOMMERCE_RES: list[tuple[str, re.Pattern[str]]] = [
    ("shopify", re.compile(r"cdn\.shopify\.com|myshopify\.com|shopify\.theme|Shopify\.theme", re.I)),
    ("woocommerce", re.compile(r"woocommerce|wp-content/plugins/woo", re.I)),
    ("magento", re.compile(r"magento|mage/cookies", re.I)),
    ("wix", re.compile(r"static\.wixstatic\.com|_wixCssModules", re.I)),
    ("squarespace", re.compile(r"squarespace\.com|static1\.squarespace", re.I)),
    ("amazon_store", re.compile(r"amazon\.(?:com|co\.uk|de|ae|sa|com\.mx|com\.br|co\.jp)/(?:shops|s\?|gp/a)", re.I)),
    ("tiktok_shop", re.compile(r"tiktok\.com/shop|shop\.tiktok", re.I)),
    ("shopee", re.compile(r"shopee\.(?:com|sg|my|ph|co\.id|th|vn|br)", re.I)),
    ("lazada", re.compile(r"lazada\.(?:com|sg|my|ph|co\.id|th|vn)", re.I)),
    ("alibaba", re.compile(r"alibaba\.com|aliexpress\.com", re.I)),
    ("ebay", re.compile(r"ebay\.(?:com|co\.uk|de|ae)", re.I)),
]

# ---------- 海外配送 ----------
_SHIPPING_RES = [
    re.compile(r"worldwide\s+(?:shipping|delivery|free)", re.I),
    re.compile(r"international\s+(?:shipping|delivery|freight)", re.I),
    re.compile(r"(?:global|international)\s+(?:shipping|delivery)\s+(?:to|from)", re.I),
    re.compile(r"ship(?:s|ping)?\s+(?:to|worldwide)\s+(?:\d+|over\s+\d+)\s+(?:countries|country)", re.I),
    re.compile(r"(?:海外|国际|全球)(?:配送|物流|发货|快递)", re.I),
]

# ---------- 海外市场国家提及（需求 §4.2 点名市场 + WA 高渗透区） ----------
# (ISO2, 英文名/别名正则)；命中记英文国名展示
_MARKET_RES: list[tuple[str, re.Pattern[str]]] = [
    ("US", re.compile(r"\b(?:USA?|United States|America(?:n)?)\b")),
    ("GB", re.compile(r"\b(?:UK|United Kingdom|Britain|British)\b")),
    ("AE", re.compile(r"\b(?:UAE|Dubai|United Arab Emirates)\b", re.I)),
    ("SA", re.compile(r"\b(?:Saudi(?:\sArabia)?|KSA|Riyadh|Jeddah)\b", re.I)),
    ("BR", re.compile(r"\b(?:Brazil|Brasil)\b", re.I)),
    ("MX", re.compile(r"\b(?:Mexico|México)\b", re.I)),
    ("ID", re.compile(r"\b(?:Indonesia|Jakarta)\b", re.I)),
    ("TH", re.compile(r"\b(?:Thailand|Bangkok)\b", re.I)),
    ("MY", re.compile(r"\b(?:Malaysia|Kuala Lumpur)\b", re.I)),
    ("SG", re.compile(r"\b(?:Singapore)\b", re.I)),
    ("PH", re.compile(r"\b(?:Philippines|Manila)\b", re.I)),
    ("VN", re.compile(r"\b(?:Vietnam|Viet Nam)\b", re.I)),
    ("DE", re.compile(r"\b(?:Germany|Deutschland)\b", re.I)),
    ("FR", re.compile(r"\b(?:France|French)\b", re.I)),
    ("AU", re.compile(r"\b(?:Australia|Sydney|Melbourne)\b", re.I)),
    ("CA", re.compile(r"\b(?:Canada|Toronto)\b", re.I)),
    ("JP", re.compile(r"\b(?:Japan|Tokyo)\b", re.I)),
    ("KR", re.compile(r"\b(?:Korea|Seoul)\b", re.I)),
    ("IN", re.compile(r"\b(?:India|Mumbai|Delhi)\b", re.I)),
    ("NG", re.compile(r"\b(?:Nigeria|Lagos)\b", re.I)),
    ("EG", re.compile(r"\b(?:Egypt|Cairo)\b", re.I)),
    ("TR", re.compile(r"\b(?:Turkey|Türkiye|Istanbul)\b", re.I)),
    ("RU", re.compile(r"\b(?:Russia|Moscow)\b", re.I)),
    ("ES", re.compile(r"\b(?:Spain|Madrid)\b", re.I)),
    ("IT", re.compile(r"\b(?:Italy|Milan|Rome)\b", re.I)),
    ("NL", re.compile(r"\b(?:Netherlands|Amsterdam|Holland)\b", re.I)),
    ("QA", re.compile(r"\b(?:Qatar|Doha)\b", re.I)),
    ("KW", re.compile(r"\b(?:Kuwait)\b", re.I)),
]

# ---------- 出海自述 ----------
_EXPORT_WORDS_RES = [
    re.compile(r"\bglobal\s+(?:supplier|seller|brand|manufacturer|company|partner)\b", re.I),
    re.compile(r"\bexport(?:s|ing)?\s+to\s+\w+", re.I),
    re.compile(r"\bserving\s+(?:customers|clients)\s+(?:worldwide|globally|across)\b", re.I),
    re.compile(r"\binternational\s+(?:buyer|customer|client|market|business)s?\b", re.I),
    re.compile(r"(?:出口|出海|海外市场|跨境)(?:到|遍布|覆盖)?", re.I),
]

# 中文页面的「中文语境」豁免：中文站文本天然命中 China/Chinese 等词不在此检测范围
# （我们只检海外市场名，不检 China），无需额外处理。

OVERSEAS_LABELS_ZH: dict[str, str] = {
    "currencies": "海外货币",
    "languages": "多语言版本",
    "ecommerce": "电商平台",
    "shipping": "海外配送",
    "markets": "海外市场提及",
    "export_words": "出海自述",
    "domain_tld": "海外域名",
}

# 海外 ccTLD（§4.2「海外域名」）：官网注册在海外国家顶级域 = 出海经营证据
_OVERSEAS_CCTLD = {
    "sg", "my", "id", "th", "ph", "vn", "jp", "kr", "in", "pk", "bd",
    "ae", "sa", "qa", "kw", "om", "bh", "tr", "ng", "ke", "gh", "za", "eg",
    "ma", "br", "mx", "co", "ar", "cl", "pe", "gb", "de", "fr", "it", "nl",
    "es", "pt", "pl", "au", "nz", "us", "ca",
}


def detect_domain_tld(website: str | None) -> str | None:
    """官网域名的海外 ccTLD（返回如 'sg'/'my'；.com 等 gTLD 或 .cn 返回 None）。"""
    if not website:
        return None
    from app.collectors.normalize import extract_domain

    domain = (extract_domain(website) or "").lower()
    if not domain or "." not in domain:
        return None
    tld = domain.rsplit(".", 1)[-1]
    if tld in _OVERSEAS_CCTLD:
        return tld
    return None


def _dedup_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def detect_overseas_signals(html_list: list[str | None]) -> dict[str, list[str]]:
    """页面集合 → 出海信号（{键: [证据串]}，空命中键省略）。

    纯函数无 IO； Markets 只数英文国名（中文语境的「美国」等由 zhCN 词表
    覆盖会引入误报——中文站提「美国」多为资讯而非市场，暂不检测中文国名）。
    """
    joined = "\n".join(h for h in html_list if h)
    if not joined:
        return {}

    out: dict[str, list[str]] = {}

    currencies = [code for code, rx in _CURRENCY_RES if rx.search(joined)]
    if currencies:
        out["currencies"] = currencies

    langs = {m.group(1).lower() for m in _HREFLANG_RE.finditer(joined)}
    langs |= {m.group(1).lower() for m in _LANG_SWITCH_RE.finditer(joined)}
    # 主站语言无法从 HTML 稳定判定；≥2 个语言路径/hreflang 即认为多语言版本
    if len(langs) >= 2:
        out["languages"] = sorted(langs)

    ecommerce = [name for name, rx in _ECOMMERCE_RES if rx.search(joined)]
    if ecommerce:
        out["ecommerce"] = ecommerce

    shipping = [
        m.group(0)[:60].strip()
        for rx in _SHIPPING_RES
        for m in [rx.search(joined)]
        if m
    ]
    if shipping:
        out["shipping"] = _dedup_keep_order(shipping)

    markets = [iso for iso, rx in _MARKET_RES if rx.search(joined)]
    if markets:
        out["markets"] = markets

    export_words = [
        m.group(0)[:60].strip()
        for rx in _EXPORT_WORDS_RES
        for m in [rx.search(joined)]
        if m
    ]
    if export_words:
        out["export_words"] = _dedup_keep_order(export_words)

    return out


def overseas_signal_count(signals: dict[str, list[str]] | None) -> int:
    """出海信号强度：命中类数（0-6），评分维度输入。"""
    return len(signals or {})


# ---------- 出海业务类型（PRD §8 出海画像，2026-08-31 巡检接线） ----------

# 行业里的电商语义词（export_type 判定用；与 recommend 的推荐词表口径一致但独立维护——
# 一个是画像分类，一个是产品推荐条件，耦合后改哪个都会误伤另一个）
_ECOM_INDUSTRY_TOKENS = (
    "电商", "零售", "电子", "美妆", "服装", "家居", "玩具", "母婴", "箱包",
    "e-commerce", "retail", "shopping", "consumer",
)
# 海外语义招聘信号（与 icp.OVERSEAS_JOB_KEYS 同口径，本地定义避免 collectors 互相 import）
_SERVICE_JOB_KEYS = ("overseas_cs", "overseas_sales")
_MARKETING_JOB_KEYS = ("social_ops", "wa_ops")


def derive_export_type(
    *,
    industry: str | None = None,
    overseas_signals: dict[str, list[str]] | None = None,
    target_countries: list[str] | None = None,
    job_signals: dict[str, Any] | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> str | None:
    """出海业务类型（跨境电商 / 品牌出海 / 出海服务 / 出海营销），行属性纯函数。

    前置：必须有出海证据（官网信号 / 投放国 / 海外岗 / 在投广告），否则返回
    None 不硬分类——cn_domestic 与 foreign 行不做画像。优先级：
    电商栈或电商行业 → 跨境电商；多语言/多市场/在投广告 → 品牌出海；
    海外客服/海外销售岗 → 出海服务；海外社媒/WA 运营岗 → 出海营销。
    在 apply_score 与评分/ICP 同点重算，保证与行属性始终一致。
    """
    signals = overseas_signals or {}
    job_keys = set(job_signals or {})
    source_names = {r.get("source") for r in (sources or []) if isinstance(r, dict)}

    overseas_jobs = job_keys & {*_SERVICE_JOB_KEYS, *_MARKETING_JOB_KEYS}
    has_reach = (
        bool(signals)
        or bool(target_countries)
        or bool(overseas_jobs)
        or "meta_ads" in source_names
    )
    if not has_reach:
        return None

    ecommerce_hit = bool(signals.get("ecommerce")) or any(
        tok in (industry or "").lower() for tok in _ECOM_INDUSTRY_TOKENS
    )
    if ecommerce_hit:
        return "跨境电商"

    market_reach = (
        len(signals.get("languages") or []) >= 2
        or len(signals.get("markets") or []) >= 2
        or len(target_countries or []) >= 2
        or "meta_ads" in source_names
        or bool(signals.get("export_words"))
    )
    if market_reach:
        return "品牌出海"

    if job_keys & set(_SERVICE_JOB_KEYS):
        return "出海服务"
    if job_keys & set(_MARKETING_JOB_KEYS):
        return "出海营销"
    return None
