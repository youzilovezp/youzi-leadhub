"""ICP 二重门（2026-08-31 业务重构）：客户只面向「中国企业 × 出海业务」。

资格判定是销售池的准入门，不是评分里的软加权：
- qualified    中国企业证据 + 出海信号 → 进入销售池与每日商机批次
- cn_domestic  中国企业证据、未见出海信号 → 培育池（补官网富化后可能升级）
- foreign      有评估结论但无任何中国企业证据 → 不进默认销售池
- unknown      证据不足（未富化/无从判断）→ 保留在池，标记待验证

中国企业证据（任一命中即视为 CN，防纯英文站误杀）：
- is_cn：来源标记（中国招聘站 / CSV 中国种子 / meta_ads 中文页名文案 /
  官网中文内容 ≥30% / ICP 备案号——各写入方维护）
- country == CN
- phone_e164 以 +86 开头

出海证据（任一命中）：overseas_signals 非空 / fb_whatsapp（FB 私域）/
target_countries 非空（meta_ads 投放国家）/ 招聘信号含海外语义岗
（wa_ops=WhatsApp 运营、overseas_cs=海外客服、overseas_sales=海外销售、
social_ops=海外社媒运营——在招海外岗位 = 出海业务在运转的直接证据；
2026-08-31 巡检补：jobui 通道 24 家真实出海企业因无官网证据卡死培育池，
官网发现命中率救不回这个缺口）。

「有评估结论」= enriched_at 非空（官网已抓取且无中文/无 CN 特征）或
来源含 meta_ads **且有官网**（页名文案已做中文判定）——只有评估过才有资格
说 foreign，从未富化的行保持 unknown，不做有罪推定。

2026-08-31 审计修正：meta_ads 来源 + 无官网 + 无 CN 证据此前直接判 foreign——
中国出海大卖普遍英文品牌英文素材，探测失败（登录墙）拿不到官网时没有任何
翻案通道（无官网=富化不了=永远 foreign），恰恰是最高价值客群被不可见地丢弃。
现口径：无官网的 meta_ads 行保持 unknown（可见、待验证），等官网发现补全并
富化后再评估。
"""

from __future__ import annotations

from typing import Any

ICP_STATUS_VALUES = ("qualified", "cn_domestic", "foreign", "unknown")

# 招聘信号里的海外语义键（§4.3 分类器产出）：命中任一 = 出海业务在运转。
# crm_ops 不算——运营 CRM 不必然服务海外市场。
OVERSEAS_JOB_KEYS = ("wa_ops", "overseas_cs", "overseas_sales", "social_ops")

ICP_STATUS_LABELS_ZH: dict[str, str] = {
    "qualified": "中国出海",
    "cn_domestic": "中国·未出海",
    "foreign": "非中国企业",
    "unknown": "待验证",
}


def has_cn_evidence(*, is_cn: bool, country: str | None, phone_e164: str | None) -> bool:
    """中国企业证据：来源标记 / 国家码 / +86 号码，任一命中。"""
    return bool(is_cn) or (country or "").upper() == "CN" or bool(phone_e164 and phone_e164.startswith("+86"))


def has_overseas_evidence(
    *,
    overseas_signals: dict[str, list[str]] | None,
    fb_whatsapp: bool,
    target_countries: list[str] | None,
    job_signals: dict[str, Any] | None = None,
) -> bool:
    """出海证据：官网出海信号 / FB 私域（CTWA 代理）/ 投放国家 / 在招海外岗，任一命中。"""
    return (
        bool(overseas_signals)
        or bool(fb_whatsapp)
        or bool(target_countries)
        or any(k in (job_signals or {}) for k in OVERSEAS_JOB_KEYS)
    )


def compute_icp_status(
    *,
    is_cn: bool = False,
    country: str | None = None,
    phone_e164: str | None = None,
    overseas_signals: dict[str, list[str]] | None = None,
    fb_whatsapp: bool = False,
    target_countries: list[str] | None = None,
    job_signals: dict[str, dict[str, Any]] | None = None,
    enriched_at: Any = None,
    sources: list[dict[str, Any]] | None = None,
    website: str | None = None,
) -> str:
    """ICP 资格判定纯函数（行属性输入，无 IO）。

    website 参与 evaluated 判定：meta_ads 来源无官网 = 没有富化翻案通道，
    保持 unknown 不做有罪推定（2026-08-31 审计修正，详见模块 docstring）。
    """
    cn = has_cn_evidence(is_cn=is_cn, country=country, phone_e164=phone_e164)
    overseas = has_overseas_evidence(
        overseas_signals=overseas_signals,
        fb_whatsapp=fb_whatsapp,
        target_countries=target_countries,
        job_signals=job_signals,
    )
    if cn:
        return "qualified" if overseas else "cn_domestic"
    meta_ads_seen = any(
        r.get("source") == "meta_ads" for r in (sources or []) if isinstance(r, dict)
    )
    evaluated = enriched_at is not None or (meta_ads_seen and bool(website))
    return "foreign" if evaluated else "unknown"


def compute_icp_status_of(lead: Any) -> str:
    """ORM Lead 行适配（getattr 容错，评分钩子对任意形状的行可调用）。"""
    return compute_icp_status(
        is_cn=bool(getattr(lead, "is_cn", False)),
        country=getattr(lead, "country", None),
        phone_e164=getattr(lead, "phone_e164", None),
        overseas_signals=getattr(lead, "overseas_signals", None) or None,
        fb_whatsapp=bool(getattr(lead, "fb_whatsapp", False)),
        target_countries=getattr(lead, "target_countries", None) or None,
        job_signals=getattr(lead, "job_signals", None) or None,
        enriched_at=getattr(lead, "enriched_at", None),
        sources=getattr(lead, "sources", None),
        website=getattr(lead, "website", None) or None,
    )
