"""ICP 二重门（2026-08-31 业务重构）：客户只面向「中国企业 × 出海业务」。

资格判定是销售池的准入门，不是评分里的软加权：
- qualified    中国企业证据 + 出海信号 → 进入销售池与每日商机批次
- cn_domestic  中国企业证据、未见出海信号 → 培育池（补官网富化后可能升级）
- foreign      有评估结论但无任何中国企业证据 → 不进默认销售池
- non_buyer    非目标买家（行业媒体/社区/报告页/软件页/门户导航）→
               与 foreign 一样默认不进销售视野（spec §四买家门）
- unknown      证据不足（未富化/无从判断）→ 保留在池，标记待验证

买家门（2026-08-31，第五态）：不是所有中国企业都是潜在买家——黑名单域
（行业媒体/社区/平台门户）与名称词表（资讯/社区/论坛/白皮书/下载…）任一
命中即 non_buyer，且**优先于** CN/出海证据判定：媒体站哪怕 CN+出海全占
也不进销售池。判不了（name/domain 两样都没有）不算 non_buyer——
白名单五行业是归类标签不是硬门（industry 常缺失，硬门杀召回）。

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

import re
from typing import Any

ICP_STATUS_VALUES = ("qualified", "cn_domestic", "foreign", "non_buyer", "unknown")

# 招聘信号里的海外语义键（§4.3 分类器产出）：命中任一 = 出海业务在运转。
# crm_ops 不算——运营 CRM 不必然服务海外市场。
OVERSEAS_JOB_KEYS = ("wa_ops", "overseas_cs", "overseas_sales", "social_ops")

ICP_STATUS_LABELS_ZH: dict[str, str] = {
    "qualified": "中国出海",
    "cn_domestic": "中国·未出海",
    "foreign": "非中国企业",
    "non_buyer": "非目标买家",
    "unknown": "待验证",
}

# ---------- 买家门（spec §四）：不是所有中国企业都是潜在买家 ----------

# 实测漏网域名（2026-08-31 dev 库查实：霸榜的就是这些"行业媒体/社区/平台门户"）
NON_BUYER_DOMAINS: tuple[str, ...] = (
    "ikjzd.com",         # 跨境知道（资讯站）
    "wearesellers.com",  # 知无不言（卖家社区）
    "cifnews.com",       # 雨果跨境（行业媒体/平台）
    "kuajingyan.com",    # 跨境眼
    "kjtong.com",        # 跨境通（门户导航）
    "mckinsey.com.cn",   # 咨询报告页
    "gizmodo.com",       # 海外科技媒体（软件下载页宿主）
    "whatsappbusiness.com",  # WhatsApp 官方产品页
    "letschuhai.com",    # 36氪出海（2026-09-01 实测：独立域漏网，混入且拿 qualified）
    "iciba.com",         # 爱词霸词典（同日实测：词条页整条入库）
)

# 名称词表（域边界锚定不适用于中文，用子串；宁可窄不可误杀正常企业）
# download 是唯一英文 token：软件下载页宿主（gizmodo 的 WhatsApp 下载页）
# 实测漏网，且正常买家名不含该词
_NON_BUYER_NAME_RE = re.compile(
    r"资讯|社区|论坛|白皮书|市场研究|行业报告|研究报告|下载|download|百科|导航|工具箱|协会|学会|36氪",
    re.IGNORECASE,
)


def is_non_buyer(*, name: str | None = None, domain: str | None = None) -> bool:
    """非目标买家判定：行业媒体/社区/报告页/软件页/门户导航。

    域名（含子域）与名称词表任一命中即 True。判不了（两样都没有）不算
    non_buyer——白名单五行业是归类标签不是硬门（industry 常缺失，硬门杀召回）。
    """
    d = (domain or "").lower().strip()
    if d:
        for b in NON_BUYER_DOMAINS:
            if d == b or d.endswith("." + b):
                return True
    if name and _NON_BUYER_NAME_RE.search(name):
        return True
    return False


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
    name: str | None = None,
    domain: str | None = None,
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
    name/domain 参与买家门（第五态）：黑名单命中优先于 CN/出海证据——
    缺省（None）不触发，既有调用方行为不变。
    """
    if is_non_buyer(name=name, domain=domain):
        return "non_buyer"
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
        name=getattr(lead, "name", None),
        domain=getattr(lead, "domain", None),
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


# ---------- CN 证据分级（2026-08-31 审计：CJK 启发式的系统性误判通道） ----------
#
# is_cn 的写入方分两类：
# - 强证据：country=CN / +86 号码 / 中国招聘站来源（job_posting、career_site）/
#   人工录入与种子导入（manual、seed_import——显式人为断言）
# - 弱证据：纯 CJK 启发式（web_search 中文标题 / meta_ads 中文页名文案 /
#   官网中文内容 ≥30%）——东南亚华人本地企业同样命中，是 qualified 误判的
#   主要入口。弱证据行不拒之门外（宁漏勿重反过来也伤召回），但必须可见、
#   且质量抽检优先抽它们来量化误判率。

CN_STRONG_SOURCES = ("job_posting", "career_site", "seed_import", "manual")


def cn_evidence_of(
    *,
    is_cn: bool = False,
    country: str | None = None,
    phone_e164: str | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> str:
    """CN 证据强度：""（无）/ "weak"（仅 CJK 启发式）/ "strong"（硬证据）。"""
    has_cn = has_cn_evidence(is_cn=is_cn, country=country, phone_e164=phone_e164)
    if not has_cn:
        return ""
    if (country or "").upper() == "CN" or (phone_e164 or "").startswith("+86"):
        return "strong"
    source_names = {r.get("source") for r in (sources or []) if isinstance(r, dict)}
    if source_names & set(CN_STRONG_SOURCES):
        return "strong"
    return "weak"


def cn_evidence_of_lead(lead: Any) -> str:
    """ORM Lead 行适配。"""
    return cn_evidence_of(
        is_cn=bool(getattr(lead, "is_cn", False)),
        country=getattr(lead, "country", None),
        phone_e164=getattr(lead, "phone_e164", None),
        sources=getattr(lead, "sources", None),
    )
