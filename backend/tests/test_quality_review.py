"""质量抽检闭环（§十二 验证，2026-08-31）：队列抽样 / 标注 / 指标统计。

共享测试库约束：本文件用 qualx 前缀唯一域名，不与其他文件撞。
"""

from app.collectors.base import LeadDraft
from app.crud.lead import upsert_lead
from app.schemas.collect import ContactCreate

_QA_DRAFT: dict = {
    "country": "CN",
    "is_cn": True,
    "fb_whatsapp": True,
    "whatsapp_url": "https://wa.me/8613911110001",
    "whatsapp_numbers": ["8613911110001", "8613911110002"],
    "wa_business": True,
    "target_countries": ["US", "GB", "AE"],
    "overseas_signals": {
        "currencies": ["USD"], "languages": ["EN"], "ecommerce": ["shopify"],
        "markets": ["USA"], "shipping": ["worldwide"],
    },
    "social": {"facebook": "https://facebook.com/qualx", "instagram": "https://instagram.com/qualx"},
}


async def _login(client, credentials):
    r = await client.post("/api/v1/auth/login", json=credentials)
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


async def _mk_lead(db_session, name: str, website: str):
    lead, _ = await upsert_lead(
        db_session, LeadDraft(source="meta_ads", name=name, website=website, **_QA_DRAFT)
    )
    return lead


async def test_quality_loop(client, admin_credentials, db_session):
    """抽检闭环：队列出带证据的待检线索 → 标注 → stats 出指标与目标线比对。"""
    from app.db.init_db import init_db

    await init_db()
    h = await _login(client, admin_credentials)

    lead1 = await _mk_lead(db_session, "抽检科技（深圳）有限公司", "https://qualx-one.com")
    lead2 = await _mk_lead(db_session, "质检科技（杭州）有限公司", "https://qualx-two.com")
    from app.crud.contact import create_contact

    await create_contact(db_session, lead1, ContactCreate(email="ops@qualx-one.com"))
    await db_session.commit()
    assert lead1.icp_status == "qualified" and lead1.whatsapp_hit

    # 1) whatsapp 队列：出队的行带核验证据（WA 链接/号码/官网）
    r = await client.get("/api/v1/quality/queue?field=whatsapp&size=50", headers=h)
    items = r.json()["data"]["items"]
    ids = [i["lead_id"] for i in items]
    assert lead1.id in ids or lead2.id in ids
    row = next(i for i in items if i["lead_id"] == lead1.id)
    assert row["evidence"]["whatsapp_url"] and row["evidence"]["website"]

    # 2) overseas 队列：qualified 才进待检池
    r = await client.get("/api/v1/quality/queue?field=overseas&size=50", headers=h)
    ids = [i["lead_id"] for i in r.json()["data"]["items"]]
    assert lead1.id in ids and lead2.id in ids

    # 3) contact 队列：带邮箱或电话联系人的线索出队（2026-08-31 审计扩展：
    # WA 号码联系人是建联第一入口，纯电话联系人的 lead2 也必须可抽检）
    r = await client.get("/api/v1/quality/queue?field=contact&size=50", headers=h)
    items = r.json()["data"]["items"]
    ids = [i["lead_id"] for i in items]
    assert lead1.id in ids and lead2.id in ids
    row1 = next(i for i in items if i["lead_id"] == lead1.id)
    assert any(c["email"] == "ops@qualx-one.com" for c in row1["evidence"]["contacts"])
    row2 = next(i for i in items if i["lead_id"] == lead2.id)
    assert any(c.get("phone") for c in row2["evidence"]["contacts"])

    # 4) 标注：lead1 whatsapp 判对、overseas 判对；lead2 overseas 判错
    for payload in (
        {"lead_id": lead1.id, "field": "whatsapp", "verdict": "correct"},
        {"lead_id": lead1.id, "field": "overseas", "verdict": "correct", "note": "官网多语言+USD"},
        {"lead_id": lead2.id, "field": "overseas", "verdict": "incorrect", "note": "查无海外业务"},
    ):
        r = await client.post("/api/v1/quality/review", headers=h, json=payload)
        assert r.status_code == 200
    # 非法值被拒
    r = await client.post(
        "/api/v1/quality/review", headers=h,
        json={"lead_id": lead1.id, "field": "whatsapp", "verdict": "maybe"},
    )
    assert r.status_code == 400

    # 5) 已标过的不再出队（whatsapp 队列排除 lead1）
    r = await client.get("/api/v1/quality/queue?field=whatsapp&size=50", headers=h)
    assert lead1.id not in [i["lead_id"] for i in r.json()["data"]["items"]]

    # 6) stats：whatsapp 100%（1/1 达标 90%）、overseas 50%（1/2 未达 80%）、S+A 占比有值
    r = await client.get("/api/v1/quality/stats", headers=h)
    data = r.json()["data"]
    assert data["fields"]["whatsapp"]["accuracy"] == 1.0
    assert data["fields"]["whatsapp"]["meets_target"] is True
    assert data["fields"]["overseas"]["accuracy"] == 0.5
    assert data["fields"]["overseas"]["meets_target"] is False
    assert 0 <= data["sa_ratio"]["value"] <= 1
    assert data["coverage"]["whatsapp"]["pool"] >= 2

    # 复审覆盖：lead2 overseas 改判 correct → accuracy 回到 1.0（取最新）
    r = await client.post(
        "/api/v1/quality/review", headers=h,
        json={"lead_id": lead2.id, "field": "overseas", "verdict": "correct"},
    )
    assert r.status_code == 200
    r = await client.get("/api/v1/quality/stats", headers=h)
    assert r.json()["data"]["fields"]["overseas"]["accuracy"] == 1.0

    # 清理（共享测试库；标注随 lead 级联删除）
    await db_session.delete(lead1)
    await db_session.delete(lead2)
    await db_session.commit()
