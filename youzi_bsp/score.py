"""分层打分（报告 3.5）：P0 中国出海 + 市场分组 + 触达语言。纯规则、$0、不过 LLM。"""
from __future__ import annotations

import re

ICP_BEIAN = re.compile(
    r"[京沪粤浙苏皖闽赣鲁豫鄂湘桂琼渝川黔滇陕甘青宁新津冀晋蒙辽吉黑]"
    r"ICP[备证]?\s?第?\d+号?|ICP备\d+号"
)
LANG_ATTR = re.compile(r"<html[^>]*\blang=[\"']?([a-zA-Z\-]{2,8})", re.I | re.S)
HREFLANG_ZH = re.compile(r"hreflang=[\"']?zh", re.I)
ZH_CHARS = re.compile(r"[一-鿿]")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)

# 中国公司主体名（spec 3.5 P0 第 1 强信号）：开发者名/公司名含中国城市/行业词。
# 中：深圳/上海/北京/广州/杭州/成都/... + 行业词（科技/贸易/跨境/网络/电商/实业/信息）
# 英：Shenzhen/Beijing/Shanghai + Co., Ltd/Limited/Technology/Trading/Network
_CITY_CN = ("深圳|上海|北京|广州|杭州|成都|南京|武汉|西安|苏州|天津|重庆|青岛|厦门|福州|济南|郑州|长沙|合肥|南昌|南宁|昆明|贵阳|海口|兰州|西宁|银川|乌鲁木齐|呼和浩特|沈阳|大连|哈尔滨|长春|石家庄|太原|唐山|保定|邯郸|秦皇岛|邢台|张家口|承德|沧州|廊坊|衡水|大同|阳泉|长治|晋城|朔州|晋中|运城|临汾|吕梁|包头|乌海|赤峰|通辽|鄂尔多斯|呼伦贝尔|巴彦淖尔|乌兰察布|兴安盟|锡林郭勒|阿拉善")
_INDUSTRY_CN = r"科技|贸易|跨境|网络|电商|实业|信息|通信|电子|智能|数字|文化|传媒|教育|医疗|健康|金融|投资|控股|集团|实业|实业|股份|有限公司|公司"
_CITY_EN = r"Shenzhen|Shanghai|Beijing|Guangzhou|Hangzhou|Chengdu|China"
_CORP_EN = r"Co\.?,?\s*Ltd|Limited|LLC|Inc\.?|Corporation|Corp\.?|Technology|Trading|Network|Holdings|Group"
_CHINESE_COMPANY_RE = re.compile(
    rf"({_CITY_CN})[一-鿿]*({_INDUSTRY_CN})"
    rf"|{_CITY_EN}.*({_CORP_EN})",
    re.I,
)

# 灰色行业（博彩类）：BSP 无法合规服务的坏线索——降分 + 掉出 P0 层。
# 域名子串匹配规则：keyword 前不能是字母或连字符（避免"xxcasino"/"xx-casino"
# 子串误伤），keyword 后不能是连字符（避免"casino-restaurant"描述型合法商家
# 被误判为博彩）。这样保留真博彩域 `casino.com` / `sattamatkano1.me` /
# `casinoroyale.com`（keyword 直接跟字母/数字/点/收尾），同时排除
# `las-casino-restaurant.com` / `casino-royale.com`（描述型）。
GAMBLING_PAGE = re.compile(r"\b(casino|betting|satta|matka|togel|judi|poker|博彩|赌场|真人娱乐)\b", re.I)
GAMBLING_DOMAIN = re.compile(r"(?<![a-z\-])(?:casino|betting|satta|matka|togel|judi|poker|博彩)(?![a-z\-]?\-)", re.I)

# 演示/模板站（spec 3.5：过滤模板演示站、widget 厂商 demo 页这类域名级假阳性；
# 2026-09-28 B3 补齐——此前零实现）。与博彩域名同一套边界规则：keyword 前不能是
# 字母/连字符、后不能是连字符——`demo-site.com`（描述型合法商家）放过，
# `demosite.com` / `demo.com` / `elfsight.com`（widget 厂商自家 demo 页）拦截。
# 不含 sample/test——真实商家名常用（samplestore.sg 是正经零售商），误伤代价高。
DEMO_DOMAIN = re.compile(
    r"(?<![a-z\-])(?:demo|example|template|themeforest|elfsight|tidio|getbutton)"
    r"(?![a-z\-]?\-)", re.I)

# WA 重度市场（报告 3.5 市场分组）
WA_HEAVY = {"ID", "BR", "MY", "MX", "AE", "SA", "IN", "PH", "TH", "VN", "EG", "NG", "CO", "ZA"}

# 市场分组标签（CRIT #2 修复）：spec 3.5 要求 {SEA, LATAM, MENA, EU…}，
# 原实现只返单一 ISO 国家码，销售无法按市场分批消化。WA 重度国家优先入对应
# 区域，非 WA 重度按地理归类；不匹配则 OTHER。
_MARKET_GROUP = {
    "SEA":   {"ID", "MY", "PH", "TH", "VN", "SG", "BN", "KH", "LA", "MM", "TL"},
    "LATAM": {"BR", "MX", "CO", "AR", "CL", "PE", "VE", "UY", "PY", "BO", "EC", "CR", "PA",
              "DO", "GT", "HN", "SV", "NI", "CU", "PR"},
    "MENA":  {"AE", "SA", "EG", "QA", "KW", "BH", "OM", "JO", "LB", "IQ", "IR", "YE",
              "SY", "PS", "MA", "TN", "DZ", "LY", "SD"},
    "EU":    {"DE", "FR", "NL", "IT", "ES", "GB", "PL", "SE", "DK", "FI", "NO", "BE",
              "AT", "CH", "IE", "PT", "GR", "CZ", "HU", "BG", "RO", "HR", "SK", "SI",
              "LT", "LV", "EE", "CY", "MT", "LU", "IS"},
}
_HKMO_TW = {"CN", "HK", "MO", "TW"}

# 市场分组的语言兜底（spec 3.5：号码国家码 > 页面语言/hreflang）：无号码证据时
# 按页面 lang 给粗粒度区域分组。market 字段本身不兜底——国家码必须有号码实锤，
# 不用 lang 编造（es→某具体国的映射是假精度，分组粒度才是 lang 能支撑的）。
_LANG_GROUP = {
    "id": "SEA", "ms": "SEA", "th": "SEA", "vi": "SEA", "tl": "SEA", "km": "SEA",
    "es": "LATAM", "pt": "LATAM",
    "ar": "MENA", "fa": "MENA",
    "de": "EU", "fr": "EU", "it": "EU", "nl": "EU", "pl": "EU", "tr": "EU",
    "zh": "CN",
}


def page_flags(html: str) -> dict:
    """单页打分输入信号（体积小，可在管道里跨页聚合，不必缓存整页 HTML）。

    性能（2026-09-29）：<html lang> 按规范在文档开头、hreflang <link> 只在
    <head>——这两个正则只扫头部切片，385KB 页 15ms → ~7ms。ICP/博彩在页脚
    正文，保持全文。
    """
    head = html[:4096]
    m = LANG_ATTR.search(head)
    lang = (m.group(1) if m else "").split("-")[0].lower() or None
    t = _TITLE.search(head) or _TITLE.search(html)
    title = t.group(1) if t else ""
    return {
        "lang": lang,
        "icp": bool(ICP_BEIAN.search(html)),
        "hreflang_zh": bool(HREFLANG_ZH.search(html[:65536])),
        "title_zh": bool(ZH_CHARS.search(title)),
        "gambling": bool(GAMBLING_PAGE.search(html)),
    }


def _market_group(market: str | None) -> str | None:
    if not market:
        return None
    for grp, codes in _MARKET_GROUP.items():
        if market in codes:
            return grp
    # S1 fix: CN/HK/MO/TW 落独立 CN 组（之前落 OTHER，对 P0 销售分组误导）
    if market in ("CN", "HK", "MO", "TW"):
        return "CN"
    return "OTHER"


def score_domain(flags: dict, phone_countries: list[str | None], entity: str = "",
                 developer_name: str = "") -> dict:
    """P0 判定（D1-3 实测校准 + v7 后增强）：

    强信号（任一满足且非 gray）：
    - ICP 备案号（页面自己声明，geo 伪造不了）
    - 中国 TLD（.cn/.中国）
    - lang=zh 且号码市场=CN 双确认
    - hreflang=zh 且号码市场 ∈ {CN, HK, MO, TW}（MED 双轨升格）
    - App 开发者主体含中国公司名（developer_name 含城市+行业词）

    hreflang zh / 中文标题单独（无市场复合）在跨国站上误报严重（实测：港区出口
    geo-serve 中文标题 + 多语站 hreflang 导致 25% 过杀：github/google/stripe 全中招）
    ——降级为弱加分项；双轨保留是为了不丢掉真实中国出海 zh 站点。
    """
    countries = [c for c in phone_countries if c]
    # 排序后取众数：平票时结果确定（测试可复现）
    market = max(sorted(set(countries)), key=countries.count) if countries else None
    zh_weak = flags.get("hreflang_zh") or flags.get("lang") == "zh" or flags.get("title_zh")
    gray = (bool(flags.get("gambling")) or bool(GAMBLING_DOMAIN.search(entity))
            or bool(DEMO_DOMAIN.search(entity)))   # demo/模板站同博彩处理（B3）
    # P0 第 1 强信号：App 开发者主体是中国公司（play 渠道主力）
    p0_developer = bool(developer_name and _CHINESE_COMPANY_RE.search(developer_name))
    p0_hreflang_strong = bool(flags.get("hreflang_zh") and market in _HKMO_TW)
    p0 = (
        bool(flags.get("icp"))
        or entity.lower().endswith((".cn", ".中国", ".xn--fiqs8s"))
        or (flags.get("lang") == "zh" and market == "CN")
        or p0_hreflang_strong
        or p0_developer
    ) and not gray
    score = 0
    if p0:
        score += 10
    if market in WA_HEAVY:
        score += 5
    if countries:
        score += 3
    if zh_weak and not p0:
        score += 2
    # 私域运营信号（leadhub 遗产，M2-3）：社群与业务号自述各 +2——比"挂了号码"更重的使用深度
    if flags.get("wa_group"):
        score += 2
    if flags.get("wa_business"):
        score += 2
    if gray:
        score = max(score - 8, 0)
    return {"p0": int(p0), "market": market,
            "market_group": (_market_group(market)
                             or _LANG_GROUP.get((flags.get("lang") or "").lower())),
            "lang": flags.get("lang"), "score": score,
            "developer_name": developer_name or None}
