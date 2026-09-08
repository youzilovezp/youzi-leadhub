"""销售周一早报：BSP 全链路 E2E 演示。

直接读真实 DB（data/app.db），按 score 降序列出值得联系的客户，
三问（为什么需要你 / 应该卖什么 / 应该找谁）一并展示。

用法：
    python scripts/e2e_bsp_morning.py            # 自动读 settings.DB_TYPE/SQLITE_PATH
    DB_TYPE=sqlite SQLITE_PATH=data/app.db python scripts/e2e_bsp_morning.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.intent import build_three_questions
from app.collectors.qualify_reason import compute_and_save_qualify_reason
from app.core.config import settings
from app.db.init_db import init_db
from app.db.session import async_session
from app.models.lead import Lead
from app.services.llm import llm_enabled


async def _format_money(amount: float) -> str:
    return f"¥{int(amount):,}"


async def _print_lead_row(s: AsyncSession, lead: Lead) -> None:
    """打印一行 BSP 销售可读的早报。"""
    from app.crud.contact import list_contacts
    from app.crud.lead_signals import list_signals

    contacts = await list_contacts(s, lead.id)
    signals = await list_signals(s, lead.id)
    sig_urls = {sig.signal_type: sig.evidence_url for sig in signals if sig.evidence_url}
    tq = build_three_questions(lead, contacts=contacts, signal_urls=sig_urls)

    # qualify_reason 懒算（详情页同口径）
    reason = await compute_and_save_qualify_reason(s, lead, contacts, signals, sig_urls)
    await s.flush()

    # ---------- 行：评分 + 三问一句话 ----------
    grade = lead.grade
    score = lead.score
    icp = lead.icp_status
    print(f"  #{lead.id}  {grade} {score:3d}分  ICP={icp}  {lead.name}")

    # ① 为什么需要你
    if reason:
        print(f"     💡 {reason['summary']}")
    else:
        print(f"     💡 (无 reason)")
    # ② 应该卖什么
    products = tq["what"]["products"]
    need_types = tq["what"]["need_types"]
    if products:
        names = "、".join(f"{p['name']}({p['reason'].split('，')[0]})" for p in products[:3])
        print(f"     🎯 应该卖：{names}")
    if need_types:
        types = "、".join(f"{t['label']}" for t in need_types[:3])
        print(f"        需求类型：{types}")
    # ③ 应该找谁
    who = tq["who"]
    parts = []
    if who["contacts"]:
        c = who["contacts"][0]
        contact_id = c.get("name") or "（待补全）"
        title = c.get("title") or "—"
        email = c.get("email") or ""
        if email:
            parts.append(f"{contact_id}({title}) <{email}>")
        else:
            parts.append(f"{contact_id}({title})")
    if who["whatsapp_numbers"]:
        parts.append(f"WA {who['whatsapp_numbers'][0]}")
    elif who["whatsapp_url"]:
        parts.append(f"WA {who['whatsapp_url']}")
    if parts:
        print(f"     👤 应该找：{' / '.join(parts)}")
    # next_action
    if reason:
        print(f"     ➡️  {reason['next_action']}")
    print()


async def _print_summary(s: AsyncSession) -> None:
    from sqlalchemy import func

    from app.crud import agent as crud

    total_leads = (await s.execute(select(func.count(Lead.id)))).scalar_one()
    qualified = (
        await s.execute(select(func.count(Lead.id)).where(Lead.icp_status == "qualified"))
    ).scalar_one()
    print(f"📊 库内：{total_leads} 条线索 / ICP=qualified {qualified} 条")
    print(f"   后端：uWSGI/uvicorn · SQLite/PostgreSQL · LLM={'已配置' if llm_enabled() else '降级模板'}")
    print()


async def main() -> None:
    await init_db()
    async with async_session() as s:
        print()
        print("=" * 80)
        print("  📬 销售周一早报 — BSP 出海客户清单")
        print(f"  {datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M %Z')}")
        print("=" * 80)
        await _print_summary(s)

        # ICP=qualified + 已富化的 lead 按 BSP 价值排序（score 降序）
        leads = (
            await s.execute(
                select(Lead)
                .where(Lead.icp_status == "qualified")
                .order_by(Lead.score.desc(), Lead.id.asc())
                .limit(10)
            )
        ).scalars().all()
        if not leads:
            print("  ⚠️  没有 ICP=qualified 的线索。先跑采集任务。")
            return
        print(f"🎯 今日 Top {len(leads)}（按 BSP 价值排序）")
        print()
        for lead in leads:
            await _print_lead_row(s, lead)

        # 预测汇总
        from app.crud import agent as crud

        summary = await crud.compute_forecast_summary(s)
        print("─" * 80)
        print("📈 预测（实时）")
        print(f"   加权总额：{await _format_money(summary['weighted_total'])}")
        print(f"   开口加权：{await _format_money(summary['open_weighted'])}")
        print(f"   商机数 / 开口：{summary['deal_count']} / {summary['open_deal_count']}")
        if summary["by_stage"]:
            print("   按阶段：")
            for stage, v in sorted(summary["by_stage"].items(), key=lambda kv: -kv[1]["weighted"]):
                print(
                    f"     - {stage:13s} 加权 {await _format_money(v['weighted']):>14s}  ({int(v['count'])} 个)"
                )
    print()


if __name__ == "__main__":
    asyncio.run(main())