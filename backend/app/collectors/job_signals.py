"""招聘信号分类（PRD §4.3）：岗位标题 → 信号类别 + 加分。

五类（需求口径分值）：
- wa_ops          WhatsApp 运营/客服/私域          +30
- overseas_cs     海外客服/英文客服/跨境客服        +20
- social_ops      Facebook/TikTok/海外社媒运营     +15
- crm_ops         CRM 运营/Customer Success        +10~15
- overseas_sales  海外销售                         +10

输入是岗位标题串（kalibrr job.name 等）；一个标题可命中多类（如
"English-speaking WhatsApp Support" → wa_ops + overseas_cs）。
分类结果是 job_signals JSON 列与 WhatsApp/规模维度评分的输入。
"""

from __future__ import annotations

import re

# WhatsApp 语义：标题明确在招 WhatsApp 相关岗
_WA_RE = re.compile(r"whatsapp|whats\s*app|wa\s+(?:agent|operator|admin|specialist|cs)", re.I)
_CS_RE = re.compile(
    r"customer\s*service|customer\s*support"
    r"|\bsupport\s+(?:executive|officer|agent|representative|specialist|engineer)"
    r"|cs\s+(?:agent|team|lead)|客服",
    re.I,
)
_OPS_RE = re.compile(r"operations?\b|specialist|agent|admin(?:istrator)?|运营", re.I)
_SOCIAL_RE = re.compile(r"facebook|instagram|tiktok|social\s*media|社媒|新媒体", re.I)
_MKT_OPS_RE = re.compile(r"marketing|growth|ecommerce|e-commerce|advert|投放|营销", re.I)
_OVERSEAS_RE = re.compile(
    r"overseas|international|global|english[-\s]speaking|bilingual|cross[-\s]?border"
    r"|abroad|海外|国际|英文|跨境|外贸",
    re.I,
)
_CRM_RE = re.compile(r"\bcrm\b|customer\s*success|hubspot|salesforce|客户成功", re.I)
_SALES_RE = re.compile(r"sales(?:person|rep|executive|manager|associate)?\b|business\s*development|bd\s|销售", re.I)


def classify_job_title(title: str | None) -> dict[str, dict[str, int | str]]:
    """岗位标题 → {信号键: {label, points}}。空标题返回 {}。

    判定规则（保守优先——宁可漏判不误判，误判直接抬分污染评分）：
    - wa_ops：标题含 WhatsApp 语义且是运营/客服性质岗位
    - overseas_cs：海外/英文/跨境 语义 × 客服语义
    - social_ops：社媒平台/社媒运营 语义 × (运营或营销) 语义
    - crm_ops：CRM/Customer Success 语义（岗位本身就是运营 CRM）
    - overseas_sales：海外/国际 语义 × 销售 语义
    """
    if not title:
        return {}
    out: dict[str, dict[str, int | str]] = {}
    t = title  # 已编译正则均 IGNORECASE

    if _WA_RE.search(t) and (_CS_RE.search(t) or _OPS_RE.search(t) or _MKT_OPS_RE.search(t)):
        out["wa_ops"] = {"label": "WhatsApp 运营/客服", "points": 30}
    if _OVERSEAS_RE.search(t) and _CS_RE.search(t):
        out["overseas_cs"] = {"label": "海外/英文客服", "points": 20}
    if _SOCIAL_RE.search(t) and (_OPS_RE.search(t) or _MKT_OPS_RE.search(t)):
        out["social_ops"] = {"label": "海外社媒运营", "points": 15}
    if _CRM_RE.search(t):
        out["crm_ops"] = {"label": "CRM/Customer Success 运营", "points": 12}
    if _OVERSEAS_RE.search(t) and _SALES_RE.search(t):
        out["overseas_sales"] = {"label": "海外销售", "points": 10}
    return out


def job_signal_points(signals: dict[str, dict[str, int | str]] | None) -> int:
    """信号集合 → 总加分（评分维度输入；多岗位累计时调用方先合并）。"""
    if not signals:
        return 0
    return sum(int(v.get("points", 0)) for v in signals.values() if isinstance(v, dict))  # type: ignore[arg-type]


JOB_SIGNAL_LABELS_ZH: dict[str, str] = {
    "wa_ops": "WhatsApp 运营/客服",
    "overseas_cs": "海外/英文客服",
    "social_ops": "海外社媒运营",
    "crm_ops": "CRM/Customer Success 运营",
    "overseas_sales": "海外销售",
}
