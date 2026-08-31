"""富化失败原因描述测试（用户需求：每条线索富化失败时带原因）。

- 抓取层原因分类：DNS / 连接超时 / TLS / HTTP 状态码（MockTransport 无网络）
- 失败原因分层收集 → field_meta.enrich_fail 落库 + 任务日志带原因
- 成功富化自愈清除失败标记
- 详情 API 输出 enrich_fail
"""

import httpx
import pytest

from app.collectors import website_enrich


class _CtxStub:
    def __init__(self) -> None:
        self.logs: list[tuple[str, str]] = []

    async def log(self, level: str, message: str) -> None:
        self.logs.append((level, message))

    def check_cancelled(self) -> None:
        pass


def _mock_clients(handler) -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
    transport = httpx.MockTransport(handler)
    return (
        httpx.AsyncClient(transport=transport, timeout=5),
        httpx.AsyncClient(transport=transport, timeout=5, verify=False, trust_env=False),
    )


# ---------- 原因分类（抓取层，无真实网络） ----------


@pytest.mark.asyncio
async def test_reason_dns_failure():
    async def handler(request):  # noqa: ARG001
        raise httpx.ConnectError("[Errno -2] getaddrinfo failure for dead.invalid")

    html, reasons = await website_enrich._fetch_site_detailed(_mock_clients(handler), "https://dead.invalid")
    assert html is None
    assert any("DNS 解析失败" in r for r in reasons)


@pytest.mark.asyncio
async def test_reason_http_status():
    async def handler(request):  # noqa: ARG001
        return httpx.Response(403)

    html, reasons = await website_enrich._fetch_site_detailed(_mock_clients(handler), "https://blocked.com")
    assert html is None
    assert any("HTTP 403" in r for r in reasons)


@pytest.mark.asyncio
async def test_reason_connect_timeout():
    async def handler(request):  # noqa: ARG001
        raise httpx.ConnectTimeout("timed out")

    html, reasons = await website_enrich._fetch_site_detailed(_mock_clients(handler), "https://walled.com")
    assert html is None
    assert any("连接超时" in r for r in reasons)


@pytest.mark.asyncio
async def test_reason_tls_error():
    import ssl

    async def handler(request):  # noqa: ARG001
        raise httpx.ConnectError("ssl wrong version", request=request) from ssl.SSLError("certificate verify failed")

    html, reasons = await website_enrich._fetch_site_detailed(_mock_clients(handler), "https://badcert.com")
    assert html is None
    assert any("TLS" in r for r in reasons)


# ---------- 失败落库 / 成功自愈 / 详情输出 ----------


@pytest.mark.asyncio
async def test_enrich_fail_recorded_to_field_meta(client, monkeypatch):
    """首页全层失败 → field_meta.enrich_fail 带分层原因；warn 日志同因。"""
    from app.db.session import async_session
    from app.models.lead import Lead

    async with async_session() as s:
        lead = Lead(name="失败原因测试公司", dedupe_key="domain:fail-reason.com",
                    website="https://fail-reason.com", domain="fail-reason.com")
        s.add(lead)
        await s.commit()
        lead_id = lead.id

    async def dead_site(clients, url):  # noqa: ARG001
        return None, ["DNS 解析失败（域名可能失效）", "DNS 解析失败（域名可能失效）（宽松SSL直连）"]

    async def imp_fail(url):  # noqa: ARG001
        return None, "指纹层 HTTP 403"

    monkeypatch.setattr(website_enrich, "_fetch_site_detailed", dead_site)
    monkeypatch.setattr(website_enrich, "_fetch_impersonated", imp_fail)

    ctx = _CtxStub()
    ok, reason = await website_enrich._enrich_one((None, None), ctx, lead_id, "https://fail-reason.com", None)  # noqa: SLF001
    assert ok is False
    assert "DNS 解析失败" in reason and "指纹层 HTTP 403" in reason

    async with async_session() as s:
        row = await s.get(Lead, lead_id)
        fail = (row.field_meta or {}).get("enrich_fail")
    assert fail and fail["reason"] == reason
    assert fail["website"] == "https://fail-reason.com"
    assert fail.get("updated_at")
    assert any(level == "warn" and "DNS 解析失败" in msg for level, msg in ctx.logs)


@pytest.mark.asyncio
async def test_enrich_fail_healed_on_success(client, monkeypatch):
    """富化成功 → 历史 enrich_fail 标记清除（只反映最近一次）。"""
    from app.db.session import async_session
    from app.models.lead import Lead

    async with async_session() as s:
        lead = Lead(
            name="失败自愈测试公司", dedupe_key="domain:heal-test.com",
            website="https://heal-test.com", domain="heal-test.com",
            field_meta={"enrich_fail": {"reason": "HTTP 503", "website": "https://heal-test.com"}},
        )
        s.add(lead)
        await s.commit()
        lead_id = lead.id

    async def ok_site(clients, url):  # noqa: ARG001
        return "<html><body><h1>Alive</h1><p>shipping delivery payment</p></body></html>", []

    monkeypatch.setattr(website_enrich, "_fetch_site_detailed", ok_site)

    ok, reason = await website_enrich._enrich_one((None, None), _CtxStub(), lead_id, "https://heal-test.com", None)  # noqa: SLF001
    assert ok is True and reason is None

    async with async_session() as s:
        row = await s.get(Lead, lead_id)
        assert "enrich_fail" not in (row.field_meta or {})
        assert row.enriched_at is not None


@pytest.mark.asyncio
async def test_detail_api_exposes_enrich_fail(client, admin_credentials):
    """详情 API 输出 enrich_fail（前端「富化失败」行数据源）。"""
    login = await client.post("/api/v1/auth/login", json=admin_credentials)
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    r = await client.post(
        "/api/v1/collect/leads",
        headers=headers,
        json={"name": "失败展示测试", "country": "MY", "website": "https://fail-show.com"},
    )
    lead_id = r.json()["data"]["id"]

    from app.db.session import async_session
    from app.models.lead import Lead

    async with async_session() as s:
        row = await s.get(Lead, lead_id)
        meta = dict(row.field_meta or {})
        meta["enrich_fail"] = {"reason": "连接超时（站点不可达或被墙）", "website": "https://fail-show.com"}
        row.field_meta = meta
        await s.commit()

    detail = (await client.get(f"/api/v1/collect/leads/{lead_id}", headers=headers)).json()["data"]
    assert detail["enrich_fail"]["reason"] == "连接超时（站点不可达或被墙）"
