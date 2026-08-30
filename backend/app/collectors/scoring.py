"""意向评分：布尔信号加权求和（不封顶——文档口径）。

两套 ICP 共用一张信号表：
- 海外商家（旧定位）：官网 WhatsApp 插件、在招 WA 岗位、目标地区……
- 中国出海企业（WhatsApp 商机主定位）：FB 主页带 wa.me 按钮 = 已在用 WA 做私域，
  是最强商机信号（他们正是 WhatsApp Business API 的潜在买家）。
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings

# 信号键名 → 默认分值。SCORING_WEIGHTS 环境变量按键覆盖。
DEFAULT_WEIGHTS: dict[str, int] = {
    "fb_page_whatsapp": 50,  # FB 主页带 wa.me 按钮（CTWA/私域运营证据，商机最强信号）
    "whatsapp_plugin": 40,  # 官网检测到 WhatsApp 插件/链接
    "whatsapp_job": 30,  # 在招 WhatsApp 客服/私域岗位
    "has_website": 10,  # 有官网
    "has_public_email": 10,  # 有公开邮箱
    "is_target_region": 10,  # 位于高渗透目标地区
    "has_phone": 5,  # 有电话
    "has_social": 5,  # 有社媒主页
}


def effective_weights() -> dict[str, int]:
    """默认权重 + .env SCORING_WEIGHTS 覆盖。"""
    return {**DEFAULT_WEIGHTS, **settings.SCORING_WEIGHTS}


def compute_score(
    *,
    whatsapp_hit: bool,
    whatsapp_job: bool,
    website: str | None,
    email: str | None,
    country: str | None,
    phone_raw: str | None,
    phone_e164: str | None,
    social: dict[str, Any] | None,
    fb_whatsapp: bool = False,
) -> tuple[int, dict[str, int]]:
    """返回 (总分, 命中信号明细 {信号键: 分值})。"""
    target_regions = {r.upper() for r in settings.TARGET_REGIONS}
    signals = {
        "fb_page_whatsapp": fb_whatsapp,
        "whatsapp_plugin": whatsapp_hit,
        "whatsapp_job": whatsapp_job,
        "has_website": bool(website),
        "has_public_email": bool(email),
        "is_target_region": bool(country and country.upper() in target_regions),
        "has_phone": bool(phone_e164 or phone_raw),
        "has_social": bool(social),
    }
    weights = effective_weights()
    hits = {k: weights[k] for k, hit in signals.items() if hit}
    return sum(hits.values()), hits
