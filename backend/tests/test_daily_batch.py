"""今日商机批次 + 领取线索（业务重构 2026-08-31：销售每天收到一批值得联系的客户）。

共享测试库约束：本文件全部用 dailybatch 前缀的唯一域名/用户名，不与其他文件撞。
"""

from app.collectors.base import LeadDraft
from app.crud.lead import upsert_lead
from app.models.lead import Lead

_STRONG_DRAFT = {
    "source": "meta_ads",
    "country": "CN",
    "website": None,  # 各用例单独给唯一域名
    "is_cn": True,
    "fb_whatsapp": True,
    "whatsapp_url": "https://wa.me/8613900000001",
    "whatsapp_numbers": ["8613900000001", "8613900000002"],
    "wa_business": True,
    "target_countries": ["US", "GB", "AE"],
    "overseas_signals": {
        "currencies": ["USD"], "languages": ["EN"], "ecommerce": ["shopify"],
        "markets": ["USA"], "shipping": ["worldwide"],
    },
    "social": {"facebook": "https://facebook.com/dailybatch", "instagram": "https://instagram.com/dailybatch"},
}


async def _login(client, credentials):
    r = await client.post("/api/v1/auth/login", json=credentials)
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


async def test_daily_batch_and_claim(client, admin_credentials, db_session):
    h = await _login(client, admin_credentials)

    # 1) 手工录入低分中国企业（后续 meta_ads 合并升分 → 触发新晋 S/A 事件）
    r = await client.post(
        "/api/v1/collect/leads",
        headers=h,
        json={"name": "今日批次晋级科技（杭州）有限公司", "country": "CN", "website": "https://dailybatch-promote.com"},
    )
    promote_id = r.json()["data"]["id"]
    assert r.json()["data"]["grade"] == "C"

    # 2) 直插一条今日新增的 qualified 高分线索（合并富集口径）
    fresh, _ = await upsert_lead(
        db_session,
        LeadDraft(
            name="今日批次新商机科技（深圳）有限公司",
            **{**_STRONG_DRAFT, "website": "https://dailybatch-fresh.com"},
        ),
    )
    await db_session.commit()
    assert fresh.icp_status == "qualified"
    assert fresh.score >= 60  # 高分商机段（S/A）

    # 合并升分：同一企业（同 domain）再来强信号 draft → 重评 → 等级 C→A/S + 事件
    promoted_lead, _ = await upsert_lead(
        db_session,
        LeadDraft(
            name="今日批次晋级科技（杭州）有限公司",
            **{**_STRONG_DRAFT, "website": "https://dailybatch-promote.com"},
        ),
    )
    await db_session.commit()
    assert promoted_lead.id == promote_id
    assert promoted_lead.grade in ("S", "A")

    # 3) 今日批次：promoted 含晋级线索；new_leads 含新增线索（不重叠）
    r = await client.get("/api/v1/collect/leads/daily-batch", headers=h)
    data = r.json()["data"]
    promoted_ids = [x["id"] for x in data["promoted"]]
    new_ids = [x["id"] for x in data["new_leads"]]
    assert promote_id in promoted_ids
    assert fresh.id in new_ids
    assert not (set(promoted_ids) & set(new_ids))
    # 晋级事件本身计入当日高价值预警切片（grade 升 S/A is_alert=True）
    assert any(a["lead_id"] == promote_id for a in data["alerts"])

    # 三问齐备（v3 重设计）：行内带 three_questions 且 complete=True
    fresh_row = next(x for x in data["new_leads"] if x["id"] == fresh.id)
    tq = fresh_row["three_questions"]
    assert tq["complete"] is True
    assert len(tq["why"]) >= 2
    assert tq["what"]["products"]
    assert tq["who"]["contacts"] or tq["who"]["whatsapp_numbers"]

    # 齐备门槛：够分（≥60 qualified）但三问不齐备 → 不进 new_leads。
    # 构造：wa_ops 招聘 30 + 出海 15 + 独立站 10 + 3国 10 + 社媒 5 = 70/A，
    # 但无 WA 入口/无 meta_ads/无 SaaS 信号 → recommend 全不命中 → products 空
    weak, _ = await upsert_lead(
        db_session,
        LeadDraft(
            name="今日批次不齐备科技（武汉）有限公司",
            source="seed_import",
            country="CN",
            website="https://v3gate-weak3q.com",
            is_cn=True,
            whatsapp_job=True,
            job_signals={"wa_ops": {"label": "WhatsApp 运营/客服", "points": 30}},
            overseas_signals={"shipping": ["worldwide"]},
            target_countries=["US", "GB", "AE"],
            social={"facebook": "f", "instagram": "i"},
        ),
    )
    await db_session.commit()
    assert weak.score >= 60 and weak.icp_status == "qualified"
    r = await client.get("/api/v1/collect/leads/daily-batch", headers=h)
    data2 = r.json()["data"]
    assert weak.id not in [x["id"] for x in data2["new_leads"]]
    await client.delete(f"/api/v1/collect/leads/{weak.id}", headers=h)

    # 4) 领取：销售自助认领 → owner=自己、状态进「待跟进」
    r = await client.post(f"/api/v1/collect/leads/{fresh.id}/claim", headers=h)
    body = r.json()["data"]
    assert body["owner_id"] is not None
    assert body["follow_status"] == "pending"
    # 幂等：再次领取不报错
    r = await client.post(f"/api/v1/collect/leads/{fresh.id}/claim", headers=h)
    assert r.status_code == 200

    # 5) 撞单保护：另一个销售领取已被认领的线索 → 40001
    await client.post(
        "/api/v1/users",
        headers=h,
        json={"username": "dailybatch_sales", "password": "pass-123456", "nickname": "批次销售"},
    )
    r2 = await client.post("/api/v1/auth/login", json={"username": "dailybatch_sales", "password": "pass-123456"})
    h2 = {"Authorization": f"Bearer {r2.json()['data']['access_token']}"}
    r = await client.post(f"/api/v1/collect/leads/{fresh.id}/claim", headers=h2)
    assert r.status_code == 400 or r.json()["code"] == 40001

    # 清理（共享测试库）
    await client.delete(f"/api/v1/collect/leads/{fresh.id}", headers=h)
    await client.delete(f"/api/v1/collect/leads/{promote_id}", headers=h)


async def test_claim_concurrent_only_one_wins(client, admin_credentials, db_session):
    """并发领取撞单（2026-08-31 巡检修 TOCTOU）：两个销售同时领取同一共享池线索，
    原子 UPDATE 抢占保证恰好一个成功、另一个 40001——不允许后提交者覆盖。"""
    import asyncio

    h = await _login(client, admin_credentials)
    race, _ = await upsert_lead(
        db_session,
        LeadDraft(
            name="今日批次并发领取科技（南京）有限公司",
            **{**_STRONG_DRAFT, "website": "https://dailybatch-race.com"},
        ),
    )
    await db_session.commit()
    race_id = race.id  # expire_all 后属性访问会触发懒加载 IO，先缓存

    await client.post(
        "/api/v1/users",
        headers=h,
        json={
            "username": "dailybatch_race_sales",
            "password": "pass-123456",
            "nickname": "并发领取销售",
        },
    )
    r2 = await client.post(
        "/api/v1/auth/login",
        json={"username": "dailybatch_race_sales", "password": "pass-123456"},
    )
    h2 = {"Authorization": f"Bearer {r2.json()['data']['access_token']}"}

    r_admin, r_sales = await asyncio.gather(
        client.post(f"/api/v1/collect/leads/{race_id}/claim", headers=h),
        client.post(f"/api/v1/collect/leads/{race_id}/claim", headers=h2),
    )
    statuses = sorted([r_admin.status_code, r_sales.status_code])
    assert statuses == [200, 400]  # 恰好一个认领成功，另一个撞单

    # 库里 owner 唯一且是赢家之一（200 的那个）
    db_session.expire_all()
    winner = await db_session.get(Lead, race_id)
    assert winner.owner_id is not None
    winners = [
        r for r in (r_admin, r_sales) if r.status_code == 200
    ]
    assert winner.owner_id == winners[0].json()["data"]["owner_id"]

    await client.delete(f"/api/v1/collect/leads/{race_id}", headers=h)
