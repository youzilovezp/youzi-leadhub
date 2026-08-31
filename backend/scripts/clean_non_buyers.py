"""存量清洗：删买家黑名单命中的污染线索（媒体/社区/软件页/门户），备份后执行。

用法：cd backend && uv run python scripts/clean_non_buyers.py [--dry-run]

删除范围 = is_non_buyer(name, domain) 命中 或 icp_status == "non_buyer"
（collectors/icp.py 词表+域名）。预期首跑命中（2026-08-31 dev 库查实）：
雨果跨境/知无不言/跨境知道/跨境眼/跨境通/麦肯锡报告页/WhatsApp 软件页/
gizmodo 下载页 共 8 条。
备份 CSV 写 /tmp/non_buyer_backup_<date>.csv，随后显式删子表（共享库无级联的
教训：leads 删除必须先清 contacts/signals/events/follow_ups/reviews）。
"""

import argparse
import asyncio
import csv
from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.collectors.icp import is_non_buyer
from app.db.session import async_session
from app.models.lead import (
    Lead,
    LeadContact,
    LeadEvent,
    LeadFollowUp,
    LeadReview,
    LeadSignal,
)

# 先删子表再删主表（顺序无关紧要，只要都在 Lead 删除语句之前）；
# lead_reviews 的 FK 也指向 leads.id，一并清
CHILD_MODELS = (LeadContact, LeadSignal, LeadEvent, LeadFollowUp, LeadReview)


async def main(dry_run: bool) -> None:
    async with async_session() as session:
        leads = list((await session.execute(select(Lead).order_by(Lead.id))).scalars().all())
        victims = [
            lead
            for lead in leads
            if is_non_buyer(name=lead.name, domain=lead.domain) or lead.icp_status == "non_buyer"
        ]
        print(f"命中 {len(victims)} / {len(leads)} 条：")
        for lead in victims:
            print(f"  #{lead.id} {lead.name} | {lead.domain or '-'} | {lead.icp_status} | {lead.score}")
        if dry_run:
            print("（dry-run，未删除）")
            return
        if not victims:
            print("无可删除项")
            return
        backup = f"/tmp/non_buyer_backup_{datetime.now(tz=timezone.utc).date().isoformat()}.csv"
        cols = [c.name for c in Lead.__table__.columns]
        with open(backup, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for lead in victims:
                w.writerow([getattr(lead, c) for c in cols])
        # 校验备份落盘行数后再删（备份失败/缺行即中止）
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
