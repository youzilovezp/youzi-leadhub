"""按「凯越标准」清理不完整线索（2026-09-01 用户口径：信息不全的不留在池里）。

删除规则（备份 CSV 后执行，幂等）：
1. 非买家（is_non_buyer / icp=non_buyer）——同 clean_non_buyers
2. 无官网 且 非meta_ads来源 —— 永远富化不了、三问永远答不上（meta_ads 无官网行
   是最高价值的待验证人群，留给官网发现补全，不删）
3. 无任何联系方式（邮箱/电话/WA）且 无出海证据 —— 空壳行

用法：cd backend && uv run python scripts/prune_incomplete.py [--dry-run]
"""

import argparse
import asyncio
import csv
from datetime import date

from sqlalchemy import delete, select

from app.collectors.icp import is_non_buyer
from app.db.session import async_session
from app.models.lead import Lead, LeadContact, LeadEvent, LeadFollowUp, LeadReview, LeadSignal

CHILD_MODELS = (LeadContact, LeadSignal, LeadEvent, LeadFollowUp, LeadReview)


def _has_contact(l: Lead) -> bool:
    return bool(l.email or l.phone_e164 or l.whatsapp_url or (l.whatsapp_numbers or []))


def _has_overseas(l: Lead) -> bool:
    return bool(l.overseas_signals) or bool(l.target_countries) or bool(l.fb_whatsapp)


def _is_victim(l: Lead) -> bool:
    if is_non_buyer(name=l.name, domain=l.domain) or l.icp_status == "non_buyer":
        return True
    from_meta_ads = any(r.get("source") == "meta_ads" for r in (l.sources or []))
    if not (l.website or "") and not from_meta_ads:
        return True
    if not _has_contact(l) and not _has_overseas(l):
        return True
    return False


async def main(dry_run: bool) -> None:
    async with async_session() as session:
        leads = list((await session.execute(select(Lead).order_by(Lead.id))).scalars().all())
        victims = [l for l in leads if _is_victim(l)]
        reasons = {
            l.id: ("非买家" if is_non_buyer(name=l.name, domain=l.domain) or l.icp_status == "non_buyer"
                   else "无官网(非meta_ads)" if not (l.website or "")
                   else "空壳(无联系方式且无出海)")
            for l in victims
        }
        print(f"命中 {len(victims)} / {len(leads)} 条：")
        for l in victims:
            print(f"  #{l.id} {l.name[:30]} | {reasons[l.id]} | {l.grade}/{l.score}")
        if dry_run or not victims:
            print("（dry-run 或无可删项，未删除）" if dry_run else "无可删除项")
            return
        backup = f"/tmp/prune_incomplete_backup_{date.today().isoformat()}.csv"
        cols = [c.name for c in Lead.__table__.columns]
        with open(backup, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for l in victims:
                w.writerow([getattr(l, c) for c in cols])
        ids = [l.id for l in victims]
        for model in CHILD_MODELS:
            await session.execute(delete(model).where(model.lead_id.in_(ids)))
        await session.execute(delete(Lead).where(Lead.id.in_(ids)))
        await session.commit()
        print(f"已删除 {len(ids)} 条（备份 {backup}，原因分布见上方）")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    asyncio.run(main(p.parse_args().dry_run))
