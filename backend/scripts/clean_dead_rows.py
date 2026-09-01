"""存量清洗：删「永远无法建联」的死行（无官网 ∧ 无任何联系方式 ∧ 培育池）。

用法：cd backend && .venv/bin/python scripts/clean_dead_rows.py [--dry-run]

删除范围（2026-09-01 用户裁决「联系信息爬不全视为失败」）：
    icp_status = cn_domestic（培育池，来源 job_posting 品类词发现——jobui 模糊
    匹配稀释出的内贸公司）∧ 无官网 ∧ 无电话/邮箱/WA。
    无官网 → 富化无米下锅 → 联系方式永远不会出现 → 占池子污染视野。

备份 CSV 写 /tmp/dead_rows_backup_<date>.csv，随后显式删子表（共享库无级联）。
qualified/unknown 的无官网行**不删**——官网发现还能翻案。
"""

import argparse
import asyncio
import csv
from datetime import datetime, timezone

from sqlalchemy import delete, or_, select

from app.db.session import async_session
from app.models.lead import (
    Lead,
    LeadContact,
    LeadEvent,
    LeadFollowUp,
    LeadReview,
    LeadSignal,
)

CHILD_MODELS = (LeadContact, LeadSignal, LeadEvent, LeadFollowUp, LeadReview)


def _no_contact(lead: Lead) -> bool:
    wa = lead.whatsapp_numbers or []
    return not (
        (lead.phone_e164 or "").strip()
        or (lead.email or "").strip()
        or wa
        or lead.whatsapp_hit
    )


async def main(dry_run: bool) -> None:
    async with async_session() as session:
        rows = list(
            (
                await session.execute(
                    select(Lead)
                    .where(
                        Lead.icp_status == "cn_domestic",
                        or_(Lead.website.is_(None), Lead.website == ""),
                    )
                    .order_by(Lead.id)
                )
            )
            .scalars()
            .all()
        )
        victims = [lead for lead in rows if _no_contact(lead)]
        print(f"命中 {len(victims)} / 全库 cn_domestic-无官网 {len(rows)} 条：")
        for lead in victims[:10]:
            print(f"  #{lead.id} {lead.name[:30]} | {lead.domain or '-'} | {lead.score}")
        if len(victims) > 10:
            print(f"  …（其余 {len(victims) - 10} 条略）")
        if dry_run:
            print("（dry-run，未删除）")
            return
        if not victims:
            print("无可删除项")
            return
        backup = f"/tmp/dead_rows_backup_{datetime.now(tz=timezone.utc).date().isoformat()}.csv"
        cols = [c.name for c in Lead.__table__.columns]
        with open(backup, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for lead in victims:
                w.writerow([getattr(lead, c) for c in cols])
        with open(backup, encoding="utf-8-sig") as f:
            backed = sum(1 for _ in f) - 1
        if backed != len(victims):
            raise SystemExit(f"备份行数 {backed} != 命中数 {len(victims)}，中止删除")
        ids = [lead.id for lead in victims]
        for model in CHILD_MODELS:
            await session.execute(delete(model).where(model.lead_id.in_(ids)))
        await session.execute(delete(Lead).where(Lead.id.in_(ids)))
        await session.commit()
        print(f"已删除 {len(ids)} 条，备份：{backup}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    asyncio.run(main(p.parse_args().dry_run))
