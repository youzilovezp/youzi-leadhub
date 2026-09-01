"""端到端闭环实测（2026-08-31 深度巡检）：对着运行中的后端走完销售主链路。

采集侧（jobui/meta_ads→upsert→ICP→评分）已有单测与全库重评覆盖，这里验证
「今日商机 → 领取 → 跟进建联 → 成交回传」的 API 级闭环 + 三问呈现字段。

步骤：
1. admin 登录
2. 以强证据 draft 模拟 meta_ads 采集落库（新企业，今日创建）
3. GET /leads/daily-batch —— 新增高分商机切片应含该企业，且行内带
   推荐产品（卖什么）/联系人计数（找谁）/关键信号字段（为什么）
4. GET /leads/{id} —— 三问字段齐全（score_breakdown/signals/need_types/
   recommendations/sales_suggestion/contacts）
5. POST /leads/{id}/claim —— 领取（共享池 → 自己，状态 pending）
6. POST /leads/{id}/follow-up —— contacted → won（成交回传）
7. GET /collect/stats —— month_won_count +1、pipeline_health 可见
8. DELETE /leads/{id} —— 清理测试数据（事件/跟进级联）

用法：后端已在 localhost:8000 运行时，cd backend && uv run python scripts/e2e_closed_loop.py
"""

import asyncio
import os
import sys

import httpx

# 守护后端默认 :8000（scripts/dev_detached.py backend）；换端口用 E2E_BASE 覆盖
BASE = os.environ.get("E2E_BASE", "http://localhost:8000/api/v1")
E2E_DOMAIN = "e2e-closedloop-test.com"
E2E_NAME = "端到端闭环实测科技（杭州）有限公司"


def pw() -> str:
    from pathlib import Path

    for line in (Path(__file__).resolve().parent.parent / ".env").read_text().splitlines():
        if line.startswith("INITIAL_ADMIN_PASSWORD="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("INITIAL_ADMIN_PASSWORD not found in .env")


async def main() -> None:
    ok = sys.stdout
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        # 1) 登录
        r = await c.post("/auth/login", json={"username": "admin", "password": pw()})
        assert r.status_code == 200, r.text
        h = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}

        # 2) 模拟采集落库（meta_ads 强证据 draft → 走系统 upsert 路径；
        #    信号证据链/自动联系人与 meta_ads 采集器的 emit 后写入同口径）
        from app.collectors.base import LeadDraft
        from app.crud.lead import upsert_lead
        from app.crud.lead_signals import upsert_signal
        from app.db.session import async_session

        wa_numbers = ["8613911112222", "8613911113333"]
        # 预清理上次残留（幂等可重跑）
        from sqlalchemy import select as sa_select

        from app.models.lead import Lead as LeadModel

        async with async_session() as session:
            stale = (
                (await session.execute(sa_select(LeadModel).where(LeadModel.domain == E2E_DOMAIN)))
                .scalars()
                .all()
            )
            for old in stale:
                await session.delete(old)
            if stale:
                await session.commit()
                print(f"[pre] 清理上次残留 {len(stale)} 条", file=ok)

        async with async_session() as session:
            lead, created = await upsert_lead(
                session,
                LeadDraft(
                    source="meta_ads",
                    name=E2E_NAME,
                    website=f"https://{E2E_DOMAIN}",
                    country="CN",
                    is_cn=True,
                    fb_whatsapp=True,
                    whatsapp_url="https://wa.me/8613911112222",
                    whatsapp_numbers=wa_numbers,
                    wa_business=True,
                    email="sales@e2e-closedloop-test.com",
                    target_countries=["US", "GB", "AE"],
                    overseas_signals={
                        "currencies": ["USD"],
                        "languages": ["EN"],
                        "ecommerce": ["shopify"],
                        "markets": ["USA"],
                        "shipping": ["worldwide"],
                    },
                    social={
                        "facebook": "https://facebook.com/e2e-closedloop",
                        "instagram": "https://instagram.com/e2e-closedloop",
                    },
                    ad_count=6,
                ),
            )
            # 证据链（与 meta_ads.run 的 emit 后写入一致）
            profile_uri = "https://facebook.com/e2e-closedloop"
            await upsert_signal(
                session,
                lead.id,
                "meta_ad",
                "6 条在投（AE,GB,US）",
                source="meta_ads",
                evidence_url=profile_uri,
                evidence_raw="E2E ad creative sample",
                confidence=95,
            )
            await upsert_signal(
                session,
                lead.id,
                "fb_whatsapp",
                wa_numbers[0],
                source="meta_ads",
                evidence_url=profile_uri,
                evidence_raw="https://wa.me/8613911112222",
                confidence=90,
            )
            for n in wa_numbers:
                await upsert_signal(
                    session,
                    lead.id,
                    "whatsapp_number",
                    n,
                    source="meta_ads",
                    evidence_url=profile_uri,
                    evidence_raw=f"https://wa.me/{n}",
                    confidence=90,
                )
            await session.commit()
        lead_id = lead.id
        assert created
        print(
            f"[2] 采集落库：lead {lead_id}，icp={lead.icp_status} score={lead.score} grade={lead.grade} "
            f"export_type={lead.export_type} last_ad_at={bool(lead.last_ad_at)}",
            file=ok,
        )
        assert lead.icp_status == "qualified" and lead.score >= 60

        # 3) 今日商机批次
        r = await c.get("/collect/leads/daily-batch", headers=h)
        batch = r.json()["data"]
        rows = [x for x in batch["new_leads"] if x["id"] == lead_id]
        assert rows, f"e2e lead 不在今日新增高分商机切片：{batch['new_leads']}"
        row = rows[0]
        print(
            f"[3] 今日商机：new_leads 命中，推荐产品={row['recommended_products']}，"
            f"联系人数={row['contacts_count']}，job_signals={list(row.get('job_signals') or {})}，"
            f"ad_count={row.get('ad_count')}",
            file=ok,
        )
        assert row["recommended_products"], "「应该卖什么」为空"
        assert "ad_count" in row, "LeadOut 缺 ad_count"

        # 4) 详情三问
        r = await c.get(f"/collect/leads/{lead_id}", headers=h)
        d = r.json()["data"]
        print(
            f"[4] 详情三问：为什么={len(d['score_breakdown'].get('items', []))} 条加分项 / "
            f"{len(d['signals'])} 条证据链；卖什么={len(d['recommendations'])} 条推荐+"
            f"{len(d['need_types'])} 类需求；找谁={len(d['contacts'])} 联系人；"
            f"AI 建议={'有' if d.get('sales_suggestion') else '无'}",
            file=ok,
        )
        assert d["score_breakdown"]["items"] and d["signals"] and d["recommendations"]

        # 5) 领取
        r = await c.post(f"/collect/leads/{lead_id}/claim", headers=h)
        assert r.status_code == 200, r.text
        claimed = r.json()["data"]
        print(
            f"[5] 领取：owner={claimed['owner_name']} follow_status={claimed['follow_status']}",
            file=ok,
        )
        assert claimed["follow_status"] == "pending"

        # 6) 跟进 → 成交回传
        r = await c.post(
            f"/collect/leads/{lead_id}/follow-up",
            headers=h,
            json={"status": "contacted", "note": "e2e：已建联"},
        )
        assert r.status_code == 200, r.text
        r = await c.post(
            f"/collect/leads/{lead_id}/follow-up",
            headers=h,
            json={"status": "won", "note": "e2e：成交（闭环回传）"},
        )
        assert r.status_code == 200, r.text
        won = r.json()["data"]
        print(
            f"[6] 跟进→成交：follow_status={won['follow_status']} last_followed_at={bool(won['last_followed_at'])}",
            file=ok,
        )
        assert won["follow_status"] == "won"

        # 7) 统计回传
        r = await c.get("/collect/stats", headers=h)
        s = r.json()["data"]
        print(
            f"[7] 统计：month_won={s['month_won_count']} icp={s['icp_counts']} "
            f"pipeline_health={s.get('pipeline_health')}",
            file=ok,
        )
        assert s["month_won_count"] >= 1 and "pipeline_health" in s

        # 8) 清理
        r = await c.delete(f"/collect/leads/{lead_id}", headers=h)
        assert r.status_code == 200, r.text
        print("[8] 清理完成（事件/跟进随线索级联删除）", file=ok)
    print("E2E CLOSED LOOP: ALL PASS ✅")


if __name__ == "__main__":
    asyncio.run(main())
