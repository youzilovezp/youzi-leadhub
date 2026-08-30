"""今日商机批次 + 领取线索（业务重构 2026-08-31：销售每天收到一批值得联系的客户）。

共享测试库约束：本文件全部用 dailybatch 前缀的唯一域名/用户名，不与其他文件撞。
"""

from app.collectors.base import LeadDraft
from app.crud.lead import upsert_lead

_STRONG_DRAFT = dict(
    source="meta_ads",
    country="CN",
    website=None,  # 各用例单独给唯一域名
    is_cn=True,
    fb_whatsapp=True,
    whatsapp_url="https://wa.me/8613900000001",
    whatsapp_numbers=["8613900000001", "8613900000002"],
    wa_business=True,
    target_countries=["US", "GB", "AE"],
    overseas_signals={
        "currencies": ["USD"], "languages": ["EN"], "ecommerce": ["shopify"],
        "markets": ["USA"], "shipping": ["worldwide"],
    },
    social={"facebook": "https://facebook.com/dailybatch", "instagram": "https://instagram.com/dailybatch"},
)


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
