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

# 灰色行业（博彩类）：BSP 无法合规服务的坏线索——降分 + 掉出 P0 层。
# 页面词带 \b 防误伤；域名子串不带边界（sattamatkano1 必须中）——域名含
# 这些词的站几乎必是博彩，精度可接受
GAMBLING_PAGE = re.compile(r"\b(casino|betting|satta|matka|togel|judi|poker|博彩|赌场|真人娱乐)\b", re.I)
GAMBLING_DOMAIN = re.compile(r"casino|betting|satta|matka|togel|judi|poker|博彩", re.I)

# WA 重度市场（报告 3.5 市场分组）
WA_HEAVY = {"ID", "BR", "MY", "MX", "AE", "SA", "IN", "PH", "TH", "VN", "EG", "NG", "CO", "ZA"}


def page_flags(html: str) -> dict:
    """单页打分输入信号（体积小，可在管道里跨页聚合，不必缓存整页 HTML）。"""
    m = LANG_ATTR.search(html)
    lang = (m.group(1) if m else "").split("-")[0].lower() or None
    t = _TITLE.search(html)
    title = t.group(1) if t else ""
    return {
        "lang": lang,
        "icp": bool(ICP_BEIAN.search(html)),
        "hreflang_zh": bool(HREFLANG_ZH.search(html)),
        "title_zh": bool(ZH_CHARS.search(title)),
        "gambling": bool(GAMBLING_PAGE.search(html)),
    }


def score_domain(flags: dict, phone_countries: list[str | None], entity: str = "") -> dict:
    """P0 判定（D1-3 实测校准后的强信号）：

    - ICP 备案号（页面自己声明，geo 伪造不了）
    - 中国 TLD（.cn/.中国）
    - lang=zh 且号码市场=CN 双确认

    hreflang zh / 中文标题在跨国站上误报严重（实测：港区出口 geo-serve 中文标题 +
    多语站 hreflang 导致 25% 过杀：github/google/stripe 全中招）——降级为加分项。
    """
    countries = [c for c in phone_countries if c]
    # 排序后取众数：平票时结果确定（测试可复现）
    market = max(sorted(set(countries)), key=countries.count) if countries else None
    zh_weak = flags.get("hreflang_zh") or flags.get("lang") == "zh" or flags.get("title_zh")
    gray = bool(flags.get("gambling")) or bool(GAMBLING_DOMAIN.search(entity))
    p0 = (
        bool(flags.get("icp"))
        or entity.lower().endswith((".cn", ".中国", ".xn--fiqs8s"))
        or (flags.get("lang") == "zh" and market == "CN")
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
    if gray:
        score = max(score - 8, 0)
    return {"p0": int(p0), "market": market, "lang": flags.get("lang"), "score": score}
