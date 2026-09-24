"""两层 WhatsApp 检测（报告 3.3）。纯函数：HTML 进，候选出。

widget 指纹取主流 WhatsApp 挂件厂商的公开前端特征：WordPress 插件
Click to Chat（ht-ctc CSS 前缀）/ Joinchat / GetButton / Elfsight / Tidio。
SaaS widget（Elfsight/Tidio）号码运行时从厂商 API 拉取 → 静态 HTML 只记指纹（报告 v6.1）。
"""
from __future__ import annotations

import re

from parsel import Selector

LINK_PATTERNS = [
    # wa.me/<号码>（规范：完整国际号码，无 + 前缀）
    re.compile(r"wa\.me/(\+?\d[\d\s\-().–—]{5,24})", re.I),
    # send 链接三子域形态：api（插件标准）/ wp（短链跳转）/ web（人工从 WA 复制的
    # 分享链接）——leadhub 遗产：web 子域漏检致 mugroup.com hit=True 却无号码
    re.compile(r"(?:api|web|wp)\.whatsapp\.com/send/?\?[^\"'\s<>]*?phone=([^&\"'\s<>#]+)", re.I),
    re.compile(r"whatsapp://send\?[^\"'\s<>]*?phone=([^&\"'\s<>#]+)", re.I),
]

# 平台/WA widget 指纹（存在即记 domain.widget，作特征 diff 的信号之一）。
# leadhub 遗产教训：token 必须**大小写敏感 + 词边界**、在原始 HTML（非小写化）上
# 匹配——裸小写子串会打中驼峰标识符：captchaType 含 chaty / getButtonType 含
# getbutton / joinChat()（ecovacs.com 2026-09-01 探针实证，误评 +25 分）
WIDGET_RE = re.compile(
    r"\b(?:ht-ctc|joinchat|getbutton|chaty|elfsight|tidio|wp-chat|click-to-chat|whatsapp-chat)\b")

# 群组邀请链接（leadhub 遗产）：chat.whatsapp.com/xxx = 已在运营 WA 社群（私域证据）
_GROUP_RE = re.compile(r"chat\.whatsapp\.com/[A-Za-z0-9_-]{5,}", re.I)
# 页面自述在用 WhatsApp Business（业务号而非个人号）——文本出现即记信号
_WA_BUSINESS_RES = [
    re.compile(r"whatsapp\s+business", re.I),
    re.compile(r"wa\s+business\s+(?:account|number|api)", re.I),
]

_TEXT_KEYWORD = re.compile(r"whats[\s\-]?app", re.I)
_TEXT_PHONE = re.compile(r"\+?\d[\d\s\-().–—]{7,24}")


def detect(html: str) -> dict:
    """返回 {'candidates': [(raw, layer)], 'widgets': [str, ...], 'emails': [str, ...]}。

    链接层：仅扫 <a href>（parsel/lxml 解析，防把正文提到的 wa.me 文本当链接）；
    文本层：关键词 ±100 字符邻域，只收 +/00 开头的国际格式（误报控制，报告 9.2）。
    号码有效性最终由 normalize_phone（libphonenumber）裁决。
    emails：站内 mailto 富化（报告 3.1；WhatsApp 号即触达渠道，邮箱为备用渠道）。
    """
    from urllib.parse import unquote

    sel = Selector(text=html)
    candidates: list[tuple[str, str]] = []
    emails: set[str] = set()
    for href in sel.css("a::attr(href)").getall():
        for pat in LINK_PATTERNS:
            m = pat.search(href)
            if m:
                candidates.append((m.group(1), "link"))
                break
        if href.lower().startswith("mailto:"):
            addr = unquote(href[7:]).split("?")[0].strip()
            if "@" in addr and " " not in addr:
                emails.add(addr.lower())

    # widget 指纹：在原始 HTML 上大小写敏感匹配（防驼峰误报），
    # 群链接/业务号自述作为独立信号并入 widget 集合（特征 diff 用）
    widgets = set(WIDGET_RE.findall(html))
    if _GROUP_RE.search(html):
        widgets.add("wa_group")          # 已运营 WA 社群（私域证据）
    if any(r.search(html) for r in _WA_BUSINESS_RES):
        widgets.add("wa_business")       # 自述 WhatsApp Business（业务号）
    widgets = sorted(widgets)

    # ponytail: 文本层关键词命中截前 20 个——防关键词密集页拖爆扫描；邻域内号码
    # 只在国际格式（+/00 开头）时收集，本地格式留给链接层（有 wa.me 链接才算实锤）
    for m in list(_TEXT_KEYWORD.finditer(html))[:20]:
        window = html[max(0, m.start() - 100): m.end() + 100]
        for pm in _TEXT_PHONE.finditer(window):
            # 贪婪匹配可能吞下尾部分隔符（如 "+852 2123 4567 ("），剥掉再判格式
            raw = pm.group(0).strip().strip(" \t-().–—")
            if raw.startswith("+") or raw.startswith("00"):
                candidates.append((raw, "text"))

    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return {"candidates": out, "widgets": widgets, "emails": sorted(emails)}
