"""全库重键：按当前归一化口径重算所有线索的身份键（dedupe_key/namecity_key）。

用途：归一化规则变更后（2026-09-01：公司名剥中文法律后缀——「XX有限公司」
与「XX有限责任公司」自此归同一 namecity 键），把存量行的旧式键刷新到新口径；
同时收敛主键优先级 domain > tel > namecity（官网发现写入 domain 后未升键的
行会一并修正）。走 crud/lead.converge_dedupe_key（与合并路径同一函数），
撞键保留旧键，不发事件、不重评分（重评分走 reeval_leads.py）。

用法：cd backend && uv run python scripts/rekey_leads.py
"""

import asyncio

from sqlalchemy import select

from app.crud.lead import converge_dedupe_key
from app.db.session import async_session
from app.models.lead import Lead


async def main() -> None:
    async with async_session() as session:
        leads = list((await session.execute(select(Lead).order_by(Lead.id))).scalars().all())
        changed = 0
        for lead in leads:
            before = (lead.dedupe_key, lead.namecity_key)
            await converge_dedupe_key(session, lead)
            if before != (lead.dedupe_key, lead.namecity_key):
                changed += 1
                print(f"[lead {lead.id}] {lead.name}")
                print(f"  dedupe_key: {before[0]} → {lead.dedupe_key}")
        await session.commit()
        print(f"重键 {len(leads)} 条，变更 {changed} 条")


if __name__ == "__main__":
    asyncio.run(main())
