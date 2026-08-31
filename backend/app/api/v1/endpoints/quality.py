"""质量抽检（§十二 验证闭环，2026-08-31）：人工标注 → 三个准确率指标。

指标口径（标注对象 × 统计分母）：
- whatsapp  ≥90%：whatsapp_hit=True 的线索，人工核验官网 WA 入口/号码是否真实
- overseas  ≥80%：icp_status=qualified 的线索，人工核验企业是否真做海外业务
- contact   ≥60%：有联系人的线索，人工核验邮箱/电话是否有效可达
- S+A 占比 ≥20%：无需标注，grade_counts 直接算

抽样规则：各维度从「待检池」随机取样，排除已标过的（同维度有最新 verdict 的
线索不重复出队）；随机用 func.random()——PG/SQLite 同名函数，方言安全。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentUser, SessionDep
from app.models.lead import Lead, LeadContact, LeadReview
from app.schemas.common import ResponseModel

router = APIRouter()

# 维度词表：目标线（§十二）与待检池条件
REVIEW_FIELDS: dict[str, dict[str, Any]] = {
    "whatsapp": {"label": "WhatsApp 识别", "target": 0.90},
    "overseas": {"label": "出海识别", "target": 0.80},
    "contact": {"label": "联系人有效", "target": 0.60},
}
VERDICTS = ("correct", "incorrect", "unsure")


class ReviewCreate(BaseModel):
    lead_id: int
    field: str  # whatsapp / overseas / contact
    verdict: str  # correct / incorrect / unsure
    note: str | None = Field(default=None, max_length=512)


async def _reviewed_lead_ids(db: SessionDep, field: str) -> set[int]:
    """该维度已标过的线索 ID（统计取最新，出队排除用全集即可）。"""
    rows = (
        await db.execute(select(LeadReview.lead_id).where(LeadReview.field == field))
    ).all()
    return {r[0] for r in rows}


def _pool_conditions(field: str) -> list:
    """各维度的待检池（在 ICP 门内抽样——foreign/non_buyer 本就不进销售池）。"""
    conds = [Lead.icp_status.notin_(("foreign", "non_buyer"))]
    if field == "whatsapp":
        conds.append(Lead.whatsapp_hit.is_(True))
    elif field == "overseas":
        conds.append(Lead.icp_status == "qualified")
    elif field == "contact":
        # 池含邮箱或电话联系人（2026-08-31 审计：WA 号码联系人是建联第一
        # 入口、confidence 85，此前只抽邮箱联系人——最该核验的渠道不在池里）
        conds.append(
            Lead.id.in_(
                select(LeadContact.lead_id).where(
                    (LeadContact.email.is_not(None)) | (LeadContact.phone.is_not(None))
                )
            )
        )
    return conds


@router.get("/queue", summary="抽检队列（随机取样，已标过的不再出队）")
async def review_queue(
    db: SessionDep,
    user: CurrentUser,
    field: str = Query(pattern="^(whatsapp|overseas|contact)$"),
    size: int = Query(default=10, ge=1, le=50),
):
    reviewed = await _reviewed_lead_ids(db, field)
    # overseas 维多取 3 倍候选：CJK 启发式（弱 CN 证据）是 qualified 误判的
    # 主要入口（2026-08-31 审计），弱证据优先出队——先量化误判率再谈收紧
    factor = 3 if field == "overseas" else 1
    stmt = (
        select(Lead)
        .where(*_pool_conditions(field))
        .order_by(func.random())
        .limit(min((size + len(reviewed)) * factor, 400))
    )
    rows = list((await db.execute(stmt)).scalars().all())
    items = [r for r in rows if r.id not in reviewed]
    if field == "overseas":
        from app.collectors.icp import cn_evidence_of_lead

        items.sort(key=lambda r: 0 if cn_evidence_of_lead(r) == "weak" else 1)
    items = items[:size]

    # 联系人维度：带上被检联系人的邮箱/电话供核验
    contact_map: dict[int, list[dict[str, Any]]] = {}
    if field == "contact" and items:
        crows = (
            await db.execute(
                select(LeadContact)
                .where(
                    LeadContact.lead_id.in_([i.id for i in items]),
                    (LeadContact.email.is_not(None)) | (LeadContact.phone.is_not(None)),
                )
                .limit(100)
            )
        )
        contact_map = {}
        for c in crows.scalars().all():
            contact_map.setdefault(c.lead_id, []).append(
                {"email": c.email, "phone": c.phone, "name": c.name, "job_title": c.job_title}
            )

    out = []
    for lead in items:
        evidence: dict[str, Any] = {}
        if field == "whatsapp":
            evidence = {
                "whatsapp_url": lead.whatsapp_url,
                "whatsapp_numbers": (lead.whatsapp_numbers or [])[:4],
                "website": lead.website,
            }
        elif field == "overseas":
            from app.collectors.icp import cn_evidence_of_lead

            evidence = {
                "overseas_signals": {
                    k: (v or [])[:3] for k, v in (lead.overseas_signals or {}).items()
                },
                "target_countries": lead.target_countries,
                "website": lead.website,
                # weak = 仅 CJK 启发式判 CN（东南亚华人企业易误判），抽检重点
                "cn_evidence": cn_evidence_of_lead(lead),
            }
        elif field == "contact":
            evidence = {"contacts": contact_map.get(lead.id, [])}
        out.append(
            {
                "lead_id": lead.id,
                "name": lead.name,
                "grade": lead.grade,
                "score": lead.score,
                "icp_status": lead.icp_status,
                "evidence": evidence,
            }
        )

    return ResponseModel(
        data={
            "field": field,
            "label": REVIEW_FIELDS[field]["label"],
            "items": out,
            "pool_remaining_hint": max(0, len(rows) - len(items)),
        }
    )


@router.post("/review", summary="提交抽检标注")
async def submit_review(db: SessionDep, user: CurrentUser, payload: ReviewCreate):
    if payload.field not in REVIEW_FIELDS:
        from app.core.exceptions import BusinessError

        raise BusinessError(code=40001, message=f"未知抽检维度：{payload.field}")
    if payload.verdict not in VERDICTS:
        from app.core.exceptions import BusinessError

        raise BusinessError(code=40001, message=f"verdict 只能是 {VERDICTS}")
    lead = await db.get(Lead, payload.lead_id)
    if lead is None:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("线索不存在")
    review = LeadReview(
        lead_id=payload.lead_id,
        field=payload.field,
        verdict=payload.verdict,
        note=payload.note,
        reviewer_id=user.id,
    )
    db.add(review)
    await db.commit()
    return ResponseModel(data={"id": review.id})


@router.get("/stats", summary="质量指标（§十二：三准确率 + S+A 占比 vs 目标线）")
async def quality_stats(db: SessionDep, user: CurrentUser):
    """准确率 = correct / (correct + incorrect)，unsure 不进分母（无法判定≠判错）。

    每线索取最新一条（复审覆盖），SQL 不做窗口函数（跨方言），行量级小内存归并。
    """
    rows = (
        await db.execute(
            select(LeadReview.lead_id, LeadReview.field, LeadReview.verdict, LeadReview.created_at)
            .order_by(LeadReview.created_at)
        )
    ).all()
    latest: dict[tuple[int, str], str] = {}
    for lead_id, field, verdict, _ in rows:
        latest[(lead_id, field)] = verdict  # 排序后覆盖 = 留最新

    fields_out: dict[str, dict[str, Any]] = {}
    for field, meta in REVIEW_FIELDS.items():
        verdicts = [v for (lid, f), v in latest.items() if f == field]
        correct = verdicts.count("correct")
        incorrect = verdicts.count("incorrect")
        unsure = verdicts.count("unsure")
        judged = correct + incorrect
        accuracy = round(correct / judged, 4) if judged else None
        fields_out[field] = {
            "label": meta["label"],
            "target": meta["target"],
            "reviewed": len(verdicts),
            "correct": correct,
            "incorrect": incorrect,
            "unsure": unsure,
            "accuracy": accuracy,
            "meets_target": (accuracy >= meta["target"]) if accuracy is not None else None,
        }

    # S+A 占比（无需标注）：ICP 门内（非 foreign/non_buyer）池内 S+A / 全体
    grade_rows = (
        await db.execute(
            select(Lead.grade, func.count())
            .where(Lead.icp_status.notin_(("foreign", "non_buyer")))
            .group_by(Lead.grade)
        )
    ).all()
    grade_counts = dict(grade_rows)
    total = sum(grade_counts.values()) or 1
    sa_ratio = round((grade_counts.get("S", 0) + grade_counts.get("A", 0)) / total, 4)

    # 覆盖进度：已标 / 待检池规模（够不够下结论要看样本量）
    coverage = {}
    for field in REVIEW_FIELDS:
        reviewed = await _reviewed_lead_ids(db, field)
        pool = (
            await db.execute(select(func.count()).select_from(Lead).where(*_pool_conditions(field)))
        ).scalar_one()
        coverage[field] = {"reviewed": len(reviewed), "pool": pool}

    return ResponseModel(
        data={
            "fields": fields_out,
            "sa_ratio": {"value": sa_ratio, "target": 0.20, "grade_counts": grade_counts},
            "coverage": coverage,
            "note": "准确率分母不含 unsure；样本 <30 时结论不稳",
        }
    )
