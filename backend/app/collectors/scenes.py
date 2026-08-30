"""WhatsApp 使用场景 & SaaS 需求信号：官网页面文本的关键词识别。

数据来源：website_enrich 抓到的首页 + 联系页 HTML（≤3 页）。
命中只做加分项，未命中是弱证据（首页没有 ≠ 没有）——评分按 0 处理，不做负分。

关键词匹配纪律：
- ASCII 关键词必须整词命中（\\b 词边界）：\"bot\" 不能打进 \"about\"，\"sale\" 不能打进 \"wholesale\"
- 中文关键词子串命中即可（中文没有词边界概念）
"""

from __future__ import annotations

import html as html_lib
import re

# ---------- 场景关键词（WhatsApp 业务场景分类） ----------

SCENE_KEYWORDS: dict[str, list[str]] = {
    "customer_service": [
        # EN
        "customer service",
        "customer support",
        "contact us",
        "live chat",
        "support center",
        "help center",
        "after-sales",
        "order support",
        # ZH
        "客服",
        "在线客服",
        "售后服务",
        "联系我们",
        "人工客服",
    ],
    "marketing": [
        # EN
        "promotion",
        "discount",
        "coupon",
        "voucher",
        "newsletter",
        "subscribe",
        "campaign",
        # ZH
        "促销",
        "优惠",
        "折扣",
        "优惠券",
        "订阅",
        "活动",
    ],
    "transactional": [
        # EN
        "order tracking",
        "shipping",
        "delivery",
        "payment",
        "invoice",
        "checkout",
        # ZH
        "订单",
        "发货",
        "物流",
        "支付",
        "发票",
    ],
    "saas": [
        # EN
        "saas",
        "software-as-a-service",
        "book a demo",
        "free trial",
        "pricing plans",
        "api documentation",
        "integrations",
        # ZH
        "免费试用",
        "预约演示",
    ],
}

SCENE_LABELS_ZH: dict[str, str] = {
    "customer_service": "客服",
    "marketing": "营销",
    "transactional": "交易通知",
    "saas": "SaaS",
}

# ---------- SaaS 需求信号（键名与评分表对齐：scoring.D3） ----------

SAAS_SIGNALS: list[tuple[str, str, list[str]]] = [
    # (键, 中文标签, 关键词)
    (
        "crm",
        "CRM",
        [
            "crm",
            "customer relationship management",
            "salesforce",
            "hubspot",
            "zoho",
            "客户关系管理",
        ],
    ),
    (
        "helpdesk",
        "工单/客服系统",
        [
            "helpdesk",
            "help desk",
            "ticketing system",
            "zendesk",
            "freshdesk",
            "工单",
            "客服系统",
        ],
    ),
    (
        "chatbot",
        "聊天机器人",
        [
            "chatbot",
            "chat bot",
            "manychat",
            "chatfuel",
            "bot framework",
            "聊天机器人",
            "机器人客服",
        ],
    ),
    (
        "ai_service",
        "AI 客服",
        [
            "ai customer service",
            "ai chat",
            "gpt",
            "ai agent",
            "ai客服",
            "智能客服",
        ],
    ),
    (
        "marketing_automation",
        "营销自动化",
        [
            "marketing automation",
            "mailchimp",
            "klaviyo",
            "email automation",
            "营销自动化",
        ],
    ),
    (
        "omnichannel",
        "全渠道",
        [
            "omnichannel",
            "omni-channel",
            "unified inbox",
            "全渠道",
            "统一收件箱",
        ],
    ),
    # WhatsApp BSP 竞品栈（PRD §4.1「已使用其他 WhatsApp SaaS = 替换商机」）：
    # 官网/联系页出现的 BSP 品牌指纹（js 域名、widget、集成声明）——命中即
    # 说明已在用竞品做 WhatsApp 商业化，是我们最直接的替换商机
    (
        "wa_bsp",
        "WhatsApp SaaS 竞品",
        [
            "wati.io",
            "wati",
            "360dialog",
            "gupshup",
            "interakt",
            "twilio",
            "sleekflow",
            "respond.io",
            "zoko",
            "webengage",
            "vonage",
            "bird.com",
            "messagebird",
            "landbot",
            "brevo",
            "whatsapp business solution provider",
            "whatsapp api provider",
            "whatsapp business partner",
        ],
    ),
]

SAAS_LABELS_ZH: dict[str, str] = {key: label for key, label, _ in SAAS_SIGNALS}

# 页面文本提取上限（400k 字符，超长截断，防正则回溯拖垮 worker）
_MAX_TEXT = 400_000

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _contains_cjk(keyword: str) -> bool:
    """关键词是否含 CJK 字符（中文按子串命中）。"""
    return any("一" <= ch <= "鿿" for ch in keyword)


def _keyword_hit(text: str, keyword: str) -> bool:
    """单关键词命中：中文子串 / ASCII 整词（\\b 词边界）。"""
    if _contains_cjk(keyword):
        return keyword in text
    return re.search(rf"\b{re.escape(keyword)}\b", text) is not None


def page_text(html_list: list[str] | None) -> str:
    """多页 HTML → 纯文本（小写）：剥 script/style → 剥标签 → 反转义 → 压空白。"""
    if not html_list:
        return ""
    raw = "\n".join(p for p in html_list if p)
    text = _SCRIPT_STYLE_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", text)
    text = html_lib.unescape(text)
    text = _WS_RE.sub(" ", text)
    return text.lower()[:_MAX_TEXT]


def detect_scenes(html_list: list[str] | None) -> list[str]:
    """返回命中的场景键列表（无序语义，调用方按 union 合并）。"""
    text = page_text(html_list)
    if not text:
        return []
    return [scene for scene, keywords in SCENE_KEYWORDS.items() if any(_keyword_hit(text, kw) for kw in keywords)]


def detect_saas_signals(html_list: list[str] | None) -> dict[str, int]:
    """返回 {信号键: 命中关键词数}（只含命中 ≥1 的键；分值在评分层查表）。

    两层匹配：
    - 正文文本（page_text：剥 script/style/标签后）——普通关键词词边界命中
    - 原始 HTML 子串——BSP 竞品等品牌词常出现在 script src / a href 属性里，
      剥标签后全部丢失（wati.io widget、360dialog 链接），必须在 raw 里扫
    """
    text = page_text(html_list)
    raw = "\n".join(p for p in (html_list or []) if p)[:_MAX_TEXT].lower()
    if not text and not raw:
        return {}
    hits: dict[str, int] = {}
    for key, _label, keywords in SAAS_SIGNALS:
        count = 0
        for kw in keywords:
            if text and _keyword_hit(text, kw):
                count += 1
            elif key == "wa_bsp" and kw in raw:
                # 品牌词足够独特（wati.io/360dialog/gupshup…），raw 子串命中
                count += 1
        if count:
            hits[key] = count
    return hits
