"""线索动态事件：快照 diff + 统一重评钩子。

三个变更点（upsert 新建/合并、website_enrich 富化、联系人 CRUD）都走
rescore_and_log——评分重算 + 事件发射集中在这一处，避免散落调用漏传参数
（旧实现 website_enrich 重评时漏传 fb_whatsapp 的 bug 即由此结构性消除）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.scenes import SAAS_LABELS_ZH, SCENE_LABELS_ZH
from app.collectors.scoring import DIM_LABELS_ZH, apply_score
from app.models.lead import Lead, LeadContact, LeadEvent

# ---------- 事件词表 ----------

EVENT_TYPES: list[str] = [
    "source_added",  # 新来源进入（合并时新追加的 source）
    "manual_entry",  # 手工录入创建
    "whatsapp_found",  # 首次检测到 WhatsApp（官网插件/链接/FB 主页）
    "fb_whatsapp_found",  # FB 主页出现 wa.me 按钮（CTWA/私域代理信号）
    "whatsapp_job_found",  # 首次发现在招 WhatsApp 相关岗位
    "email_found",  # 首次拿到公开邮箱
    "social_found",  # 新增社媒主页
    "scene_change",  # WhatsApp 场景新增（客服/营销/交易/SaaS）
    "saas_signal_change",  # SaaS 需求信号新增（CRM/工单/Chatbot…）
    "score_change",  # 六维总分变化（含 old/new）
    "grade_change",  # 等级变化（含 old/new）
    "contact_added",  # 新增联系人
    "assigned",  # 分配/转移/释放（PRD §24）
    "opportunity_created",  # 新增商机（§37）
    "opportunity_stage",  # 商机阶段推进（§37）
]

EVENT_TYPE_LABELS_ZH: dict[str, str] = {
    "source_added": "新来源",
    "manual_entry": "手工录入",
    "whatsapp_found": "发现 WhatsApp",
    "fb_whatsapp_found": "FB 主页挂 WA 按钮",
    "whatsapp_job_found": "在招 WhatsApp 岗位",
    "email_found": "发现邮箱",
    "social_found": "新增社媒",
    "scene_change": "场景变化",
    "saas_signal_change": "SaaS 需求信号",
    "score_change": "评分变化",
    "grade_change": "等级变化",
    "contact_added": "新增联系人",
    "assigned": "分配变动",
    "opportunity_created": "新增商机",
    "opportunity_stage": "商机推进",
}

# 高价值预警事件类型（PRD §55）：等级升到 S/A、发现 WhatsApp、SaaS 信号出现
ALERT_EVENT_TYPES: set[str] = {"whatsapp_found", "fb_whatsapp_found", "saas_signal_change"}
# 哪些 grade_change 视为预警：新等级落入 S/A
ALERT_GRADES: set[str] = {"S", "A"}


def add_event(
    db: AsyncSession,
    lead: Lead,
    event_type: str,
    *,
    payload: dict[str, Any] | None = None,
    note: str | None = None,
    created_by: int | None = None,
    is_alert: bool = False,
) -> None:
    """追加一条事件（不可变记录）。is_alert=True 进预警中心。"""
    db.add(
        LeadEvent(
            lead_id=lead.id,
            event_type=event_type,
            payload=payload or {},
            note=note,
            created_by=created_by,
            is_alert=is_alert,
        )
    )


def snapshot_lead(lead: Lead) -> dict[str, Any]:
    """变更前的关键字段快照，供 diff 发射事件。"""
    return {
        "whatsapp_hit": bool(lead.whatsapp_hit),
        "fb_whatsapp": bool(lead.fb_whatsapp),
        "whatsapp_job": bool(lead.whatsapp_job),
        "email": lead.email,
        "social_keys": set((lead.social or {}).keys()),
        "scenes": set(lead.scenes or []),
        "saas_keys": set((lead.saas_signals or {}).keys()),
        "source_names": {r.get("source") for r in (lead.sources or []) if r.get("source")},
        "score": lead.score,
        "grade": lead.grade,
    }


async def contacts_summary(db: AsyncSession, lead_id: int) -> tuple[int, bool, bool]:
    """联系人数 + 是否有 tier1/tier2 决策人（联系人维度评分的输入）。"""
    rows = (
        await db.execute(
            select(LeadContact.seniority, func.count())
            .where(LeadContact.lead_id == lead_id)
            .group_by(LeadContact.seniority)
        )
    ).all()
    count = sum(c for _s, c in rows)
    tiers = {s for s, _c in rows if s}
    return count, "tier1" in tiers, "tier2" in tiers


async def rescore_and_log(
    db: AsyncSession,
    lead: Lead,
    *,
    before: dict[str, Any] | None = None,
    created_by: int | None = None,
) -> tuple[int, int, str]:
    """统一重评钩子：查联系人摘要 → 六维重评写回 → diff 发射事件。

    before 传变更前快照（_merge_into / _enrich_one 入口取）时才发字段类事件；
    只传 lead 则仅发 score/grade 变化事件。返回 (旧分, 新分, 新分级)。
    """
    count, has_tier1, has_tier2 = await contacts_summary(db, lead.id)
    old_score, new_score, new_grade = apply_score(
        lead, contacts_count=count, has_tier1=has_tier1, has_tier2=has_tier2
    )

    if before is not None:
        await diff_lead_events(db, lead, before, created_by=created_by)

    old_grade = (before or {}).get("grade") or lead.grade
    if new_score != old_score:
        add_event(
            db,
            lead,
            "score_change",
            payload={"old": old_score, "new": new_score},
            note=f"评分 {old_score} → {new_score}",
            created_by=created_by,
        )
    if new_grade != old_grade:
        old_label = old_grade or "C"
        grade_up = new_grade in ALERT_GRADES and old_label not in ALERT_GRADES
        add_event(
            db,
            lead,
            "grade_change",
            payload={"old": old_label, "new": new_grade},
            note=f"等级 {old_label} → {new_grade}，建议关注" if grade_up else f"等级 {old_label} → {new_grade}",
            created_by=created_by,
            is_alert=grade_up,  # 升到 S/A = 高价值预警（§55）
        )
    await db.flush()
    return old_score, new_score, new_grade


async def diff_lead_events(
    db: AsyncSession,
    lead: Lead,
    before: dict[str, Any],
    *,
    created_by: int | None = None,
) -> None:
    """快照 vs 当前行：字段翻转/新增 → 事件。同值不发射（防刷新噪音）。"""
    if not before.get("whatsapp_hit") and lead.whatsapp_hit:
        add_event(
            db,
            lead,
            "whatsapp_found",
            payload={"url": lead.whatsapp_url},
            note=f"检测到 WhatsApp 入口：{lead.whatsapp_url or '官网'}",
            created_by=created_by,
            is_alert=True,  # 发现 WhatsApp = 高价值预警（§55）
        )
    if not before.get("fb_whatsapp") and lead.fb_whatsapp:
        # FB 主页挂 wa.me = CTWA/私域获客证据（API 不暴露广告 CTA，这是代理信号）
        add_event(
            db,
            lead,
            "fb_whatsapp_found",
            payload={"phone": lead.phone_raw},
            note=f"FB 主页出现 WhatsApp 按钮（CTWA/私域代理信号）{('：' + lead.phone_raw) if lead.phone_raw else ''}",
            created_by=created_by,
            is_alert=True,  # CTWA 代理信号 = 高价值预警（§55）
        )
    if not before.get("whatsapp_job") and lead.whatsapp_job:
        add_event(
            db,
            lead,
            "whatsapp_job_found",
            note="发现在招 WhatsApp 相关岗位",
            created_by=created_by,
        )
    if not before.get("email") and lead.email:
        add_event(
            db,
            lead,
            "email_found",
            payload={"email": lead.email},
            note=f"发现公开邮箱：{lead.email}",
            created_by=created_by,
        )
    new_social = set((lead.social or {}).keys()) - set(before.get("social_keys") or set())
    if new_social:
        add_event(
            db,
            lead,
            "social_found",
            payload={"platforms": sorted(new_social)},
            note=f"新增社媒：{'、'.join(sorted(new_social))}",
            created_by=created_by,
        )
    new_scenes = set(lead.scenes or []) - set(before.get("scenes") or set())
    if new_scenes:
        labels = [SCENE_LABELS_ZH.get(s, s) for s in sorted(new_scenes)]
        add_event(
            db,
            lead,
            "scene_change",
            payload={"added": sorted(new_scenes)},
            note=f"新增 WhatsApp 场景：{'、'.join(labels)}",
            created_by=created_by,
        )
    new_saas = set((lead.saas_signals or {}).keys()) - set(before.get("saas_keys") or set())
    if new_saas:
        labels = [SAAS_LABELS_ZH.get(k) or k for k in sorted(new_saas)]
        add_event(
            db,
            lead,
            "saas_signal_change",
            payload={"added": sorted(new_saas)},
            note=f"新增 SaaS 需求信号：{'、'.join(labels)}",
            created_by=created_by,
            is_alert=True,  # SaaS 信号出现 = 高价值预警（§55）
        )
    new_sources = set()  # 来源只看新增 key，last_seen 刷新不算事件
    for rec in lead.sources or []:
        name = rec.get("source")
        if name and name not in (before.get("source_names") or set()):
            new_sources.add(name)
    for name in sorted(new_sources):
        add_event(
            db,
            lead,
            "source_added",
            payload={"source": name},
            note=f"新来源进入：{name}",
            created_by=created_by,
        )


def describe_dimensions(score_signals: dict[str, int] | None) -> dict[str, int]:
    """score_signals → 六维分（缺失维度补 0），详情页直读。"""
    dims = {k: 0 for k in DIM_LABELS_ZH}
    for k, v in (score_signals or {}).items():
        if k in dims:
            dims[k] = int(v)
    return dims
