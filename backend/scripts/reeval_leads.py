"""全库重评：按当前评分/ICP/出海画像口径重算所有线索。

用途：评分公式、ICP 门或 export_type 派生规则变更后，把存量行刷新到新口径
（与采集器写入路径同函数，保证一致性）。只重算行内派生字段
（score/score_signals/score_breakdown/grade/icp_status/export_type），
不发事件、不碰联系人/跟进。

用法：cd backend && uv run python scripts/reeval_leads.py
"""

import asyncio

from sqlalchemy import select

from app.collectors.scoring import apply_score
from app.crud.lead_events import contacts_summary
from app.db.session import async_session
from app.models.lead import Lead


async def main() -> None:
    async with async_session() as session:
        leads = list((await session.execute(select(Lead).order_by(Lead.id))).scalars().all())
        changed = 0
        icp_before: dict[str, int] = {}
        for lead in leads:
            icp_before[lead.icp_status or "unknown"] = icp_before.get(lead.icp_status or "unknown", 0) + 1
            before = (lead.score, lead.grade, lead.icp_status, lead.export_type)
            count, has_t1, has_t2 = await contacts_summary(session, lead.id)
            apply_score(lead, contacts_count=count, has_tier1=has_t1, has_tier2=has_t2)
            if before != (lead.score, lead.grade, lead.icp_status, lead.export_type):
                changed += 1
        await session.commit()
        icp_after: dict[str, int] = {}
        for lead in leads:
            icp_after[lead.icp_status or "unknown"] = icp_after.get(lead.icp_status or "unknown", 0) + 1
        print(f"重评 {len(leads)} 条，变更 {changed} 条")
        print(f"ICP 分布（前 → 后）：{icp_before} → {icp_after}")
        export_types: dict[str, int] = {}
        for lead in leads:
            if lead.export_type:
                export_types[lead.export_type] = export_types.get(lead.export_type, 0) + 1
        print(f"export_type 分布：{export_types or '（全部无出海证据，未分类）'}")


if __name__ == "__main__":
    asyncio.run(main())
