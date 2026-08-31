"""负证据闭环测试（2026-08-31 审计批次2）。

- 复查未复现已记录的 WhatsApp 入口 → whatsapp_gone 事件（不翻布尔列）
- 信号证据 stale_days：last_seen 距今天数（SQLite naive datetime 兼容）
"""

from datetime import datetime, timedelta, timezone

import pytest

_HOME_WITHOUT_WA = """
<html><body><h1>Acme Corp</h1><p>We sell smart watches. Order tracking available.</p></body></html>
"""


class _CtxStub:
    """TaskContext 最小桩：只需 log / check_cancelled。"""

    async def log(self, level: str, message: str) -> None:  # noqa: ARG002
        pass

    def check_cancelled(self) -> None:
        pass


@pytest.mark.asyncio
async def test_whatsapp_gone_event_emitted_and_flag_kept(client, monkeypatch):
    """复查未复现 → whatsapp_gone 事件；whatsapp_hit 保持 True（历史事实）。"""
    from sqlalchemy import select

    from app.collectors import website_enrich
    from app.db.session import async_session
    from app.models.lead import Lead, LeadEvent

    async with async_session() as s:
        lead = Lead(
            name="信号消失测试公司",
            dedupe_key="domain:gone-test.com",
            website="https://gone-test.com",
            domain="gone-test.com",
            whatsapp_hit=True,
            whatsapp_url="https://wa.me/60111222333",
        )
        s.add(lead)
        await s.commit()
        lead_id = lead.id

    async def fake_fetch_site(clients, url):  # noqa: ARG001
        return _HOME_WITHOUT_WA

    monkeypatch.setattr(website_enrich, "_fetch_site", fake_fetch_site)

    ok = await website_enrich._enrich_one((None, None), _CtxStub(), lead_id, "https://gone-test.com", None)  # noqa: SLF001
    assert ok is True

    async with async_session() as s:
        row = (await s.execute(select(Lead).where(Lead.id == lead_id))).scalar_one()
        assert row.whatsapp_hit is True  # 历史事实保留
        assert row.whatsapp_url == "https://wa.me/60111222333"
        events = (
            (
                await s.execute(
                    select(LeadEvent).where(
                        LeadEvent.lead_id == lead_id, LeadEvent.event_type == "whatsapp_gone"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert "未复现" in (events[0].note or "")


@pytest.mark.asyncio
async def test_no_gone_event_when_whatsapp_still_there(client, monkeypatch):
    """仍检测到 WhatsApp → 不发 gone；whatsapp_hit 早已 True 也不重发 found。"""
    from sqlalchemy import select

    from app.collectors import website_enrich
    from app.db.session import async_session
    from app.models.lead import Lead, LeadEvent

    async with async_session() as s:
        lead = Lead(
            name="信号仍在测试公司",
            dedupe_key="domain:still-wa.com",
            website="https://still-wa.com",
            domain="still-wa.com",
            whatsapp_hit=True,
            whatsapp_url="https://wa.me/60999888777",
        )
        s.add(lead)
        await s.commit()
        lead_id = lead.id

    async def fake_fetch_site(clients, url):  # noqa: ARG001
        return '<html><body><a href="https://wa.me/60999888777">Chat</a></body></html>'

    monkeypatch.setattr(website_enrich, "_fetch_site", fake_fetch_site)
    ok = await website_enrich._enrich_one((None, None), _CtxStub(), lead_id, "https://still-wa.com", None)  # noqa: SLF001
    assert ok is True

    async with async_session() as s:
        events = (
            (
                await s.execute(
                    select(LeadEvent).where(
                        LeadEvent.lead_id == lead_id,
                        LeadEvent.event_type.in_(["whatsapp_gone", "whatsapp_found"]),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert events == []


@pytest.mark.asyncio
async def test_detail_signals_include_stale_days(client, admin_credentials):
    """详情 API 信号带 stale_days；120 天前的 last_seen → ≥90（SQLite naive 兼容）。"""
    login = await client.post("/api/v1/auth/login", json=admin_credentials)
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    r = await client.post(
        "/api/v1/collect/leads",
        headers=headers,
        json={"name": "陈旧信号测试", "country": "MY", "website": "https://stale-test.com"},
    )
    lead_id = r.json()["data"]["id"]

    from app.db.session import async_session
    from app.models.lead import LeadSignal

    async with async_session() as s:
        s.add(
            LeadSignal(
                lead_id=lead_id,
                signal_type="whatsapp_number",
                value="60123456789",
                source="website_enrich",
                confidence=95,
                last_seen=datetime.now(timezone.utc) - timedelta(days=120),
            )
        )
        s.add(
            LeadSignal(
                lead_id=lead_id,
                signal_type="fb_whatsapp",
                value="button",
                source="meta_ads",
                confidence=90,
            )
        )
        await s.commit()

    detail = (await client.get(f"/api/v1/collect/leads/{lead_id}", headers=headers)).json()["data"]
    by_type = {s_["signal_type"]: s_ for s_ in detail["signals"]}
    assert by_type["whatsapp_number"]["stale_days"] >= 90
    assert by_type["fb_whatsapp"]["stale_days"] is not None and by_type["fb_whatsapp"]["stale_days"] < 90
