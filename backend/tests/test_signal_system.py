"""信号体系测试（PRD §4 补课）：出海信号检测 / 招聘信号分类 / 评分新输入 / 证据链。"""

import pytest

# ---------- §4.2 出海信号检测 ----------


def test_detect_overseas_signals_currency_and_ecommerce():
    """海外货币 + 电商建站栈：跨境电商站典型首页。"""
    from app.collectors.overseas import detect_overseas_signals

    html = """
    <html><head>
    <link href="https://cdn.shopify.com/s/theme.css" rel="stylesheet">
    <script src="https://connect.facebook.net/sdk.js"></script>
    </head><body>
    <p>Price: USD 29.99 (AED 110)</p>
    <a href="https://wa.me/60123456789">Chat on WhatsApp</a>
    <a href="https://www.amazon.com/shops/acme">Our Amazon Store</a>
    <p>We offer worldwide shipping to over 30 countries</p>
    </body></html>
    """
    sig = detect_overseas_signals([html])
    assert "USD" in sig.get("currencies", [])
    assert "AED" in sig.get("currencies", [])
    assert "shopify" in sig.get("ecommerce", [])
    assert "amazon_store" in sig.get("ecommerce", [])
    assert sig.get("shipping")  # worldwide shipping 命中


def test_detect_overseas_signals_multilang_and_markets():
    """多语言版本（hreflang ≥2）+ 海外市场提及。"""
    from app.collectors.overseas import detect_overseas_signals

    html = """
    <html><head>
    <link rel="alternate" hreflang="en" href="https://acme.com/en">
    <link rel="alternate" hreflang="ar" href="https://acme.com/ar">
    </head><body>
    <p>Trusted supplier in USA, UK, UAE and Saudi Arabia. Export to Brazil.</p>
    </body></html>
    """
    sig = detect_overseas_signals([html])
    assert set(sig.get("languages", [])) >= {"en", "ar"}
    markets = set(sig.get("markets", []))
    assert {"US", "GB", "AE", "SA", "BR"} <= markets
    assert sig.get("export_words")  # Export to ... 命中


def test_detect_overseas_signals_empty_for_domestic_page():
    """纯国内/无信号页 → 空（美元符号单出现不算——需伴随数字或代码）。"""
    from app.collectors.overseas import detect_overseas_signals

    html = "<html><body><p>Welcome to our website. Contact us for more info.</p></body></html>"
    assert detect_overseas_signals([html]) == {}


def test_detect_overseas_signals_currency_symbol_with_digits():
    """货币符号伴随数字：$ 99 / RM150 / ¥3000 命中对应代码。"""
    from app.collectors.overseas import detect_overseas_signals

    html = "<p>Now only $ 99! RM150 in Malaysia stores. Retail ¥3000</p>"
    sig = detect_overseas_signals([html])
    assert "USD" in sig.get("currencies", [])
    assert "MYR" in sig.get("currencies", [])
    assert "JPY" in sig.get("currencies", [])


# ---------- §4.3 招聘信号分类 ----------


def test_classify_job_title_whatsapp_ops():
    """WhatsApp 客服/运营岗 → wa_ops（+30 口径最强意向）。"""
    from app.collectors.job_signals import classify_job_title, job_signal_points

    sigs = classify_job_title("WhatsApp Customer Service Agent")
    assert "wa_ops" in sigs
    assert job_signal_points(sigs) >= 30

    sigs2 = classify_job_title("WhatsApp 私域运营专员")
    assert "wa_ops" in sigs2


def test_classify_job_title_overseas_cs_and_social():
    """海外客服 / 社媒运营 / CRM / 海外销售 各自命中。"""
    from app.collectors.job_signals import classify_job_title

    assert "overseas_cs" in classify_job_title("International Customer Service Representative")
    assert "overseas_cs" in classify_job_title("English-speaking Support Executive")
    assert "social_ops" in classify_job_title("TikTok Marketing Operations Specialist")
    assert "crm_ops" in classify_job_title("CRM Administrator")
    assert "overseas_sales" in classify_job_title("Overseas Sales Manager")


def test_classify_job_title_no_false_positive():
    """普通国内岗位不误判（宁漏勿误——误判直接抬分污染评分）。"""
    from app.collectors.job_signals import classify_job_title

    for title in (
        "Retail Sales Associate", "Accountant", "Driver",
        "Warehouse Staff", "行政前台",
    ):
        assert classify_job_title(title) == {}, title


# ---------- 评分新输入（v3 加分制口径） ----------


def test_scoring_three_plus_countries_bonus():
    """§4.2 规则：投放/提及 ≥3 国 → three_markets +10（<3 国不给分）。"""
    from app.collectors.scoring import score_lead_inputs

    total_2, items_2, _ = score_lead_inputs(is_cn=True, target_countries=["US", "GB"])
    total_5, items_5, _ = score_lead_inputs(
        is_cn=True, target_countries=["US", "GB", "AE", "SA", "BR"]
    )
    assert total_2 == 0 and items_2 == []
    assert total_5 == 10
    assert [it["key"] for it in items_5] == ["three_markets"]


def test_scoring_overseas_signals_and_ad_count():
    """出海证据非空 → overseas_biz +15；在投广告 → meta_ads_running +15；
    whatsapp_job 列与 job_signals.wa_ops 同一事实只计一次 +30。"""
    from app.collectors.scoring import score_lead_inputs

    total_base, items_base, _ = score_lead_inputs(is_cn=True)
    assert total_base == 0 and items_base == []

    total_ov, items_ov, _ = score_lead_inputs(
        is_cn=True,
        overseas_signals={"currencies": ["USD"], "ecommerce": ["shopify"], "markets": ["US"]},
    )
    # 出海证据非空即 +15（v3 不按类数梯度；markets 仅 1 国不触发 three_markets）
    assert total_ov == 15
    assert {it["key"] for it in items_ov} == {"overseas_biz"}

    # 同事实不双计：whatsapp_job 与 wa_ops 是同一招聘事实，只计 wa_ops_job +30
    total_wa, items_wa, _ = score_lead_inputs(
        is_cn=True, whatsapp_job=True, job_signals={"wa_ops": {"label": "x", "points": 30}}
    )
    assert total_wa == 30
    assert [it["key"] for it in items_wa] == ["wa_ops_job"]

    # 在投证据（meta_ads 来源或 ad_count>0）→ +15；无 fb_whatsapp 不构成 CTWA
    for kwargs in ({"ad_count": 6, "sources": [{"source": "meta_ads"}]}, {"ad_count": 2}):
        total_ad, items_ad, _ = score_lead_inputs(is_cn=True, **kwargs)
        assert total_ad == 15
        assert [it["key"] for it in items_ad] == ["meta_ads_running"]


def test_scoring_backward_compatible_no_new_inputs():
    """不传新输入 → 与 v3 口径一致（迁移回填/旧行为不变）。"""
    from app.collectors.scoring import score_lead_inputs

    total, items, grade = score_lead_inputs(is_cn=True, whatsapp_hit=True)
    assert 0 <= total <= 100
    assert [(it["key"], it["points"]) for it in items] == [("site_whatsapp", 25)]
    assert grade in ("S", "A", "B", "C")


# ---------- §4.1 证据链 ----------


@pytest.mark.asyncio
async def test_signal_evidence_upsert_and_list(client):
    """证据 upsert 幂等：同 (lead, type, value) 二写只刷 last_seen 不重复。"""
    from app.db.session import async_session
    from app.models.lead import Lead

    # 建线索（client fixture 已触发 init_db，表结构就绪）
    async with async_session() as s:
        lead = Lead(name="证据链测试公司", dedupe_key="tel:+60111222333")
        s.add(lead)
        await s.commit()
        lead_id = lead.id

    from app.crud.lead_signals import list_signals, upsert_signal

    async with async_session() as s:
        created1 = await upsert_signal(
            s, lead_id, "whatsapp_number", "60123456789",
            source="website_enrich", evidence_url="https://acme.com/contact",
            evidence_raw="https://wa.me/60123456789", confidence=95,
        )
        created2 = await upsert_signal(
            s, lead_id, "whatsapp_number", "60123456789",
            source="meta_ads", confidence=90,
        )
        await s.commit()
    assert created1 is True and created2 is False

    async with async_session() as s:
        rows = await list_signals(s, lead_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.signal_type == "whatsapp_number"
    assert row.value == "60123456789"
    assert row.evidence_url == "https://acme.com/contact"
    assert row.confidence == 95  # 高置信度不被低置信度覆盖


@pytest.mark.asyncio
async def test_detail_api_returns_signals_and_overseas(client, admin_credentials):
    """详情 API 输出信号体系新字段（出海/招聘/广告 + 证据链数组）。"""
    import json as _json

    login = await client.post("/api/v1/auth/login", json=admin_credentials)
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    r = await client.post(
        "/api/v1/collect/leads",
        headers=headers,
        json={"name": "信号体系API测试", "country": "MY", "website": "https://sigtest.com"},
    )
    lead_id = r.json()["data"]["id"]

    # 直接写一条信号证据 + 出海信号，验证详情输出
    from sqlalchemy import select

    from app.db.session import async_session
    from app.models.lead import Lead

    async with async_session() as s:
        from app.crud.lead_signals import upsert_signal

        row = (await s.execute(select(Lead).where(Lead.id == lead_id))).scalar_one()
        row.overseas_signals = {"currencies": ["USD"], "markets": ["US", "GB", "AE"]}
        row.job_signals = {"wa_ops": {"label": "WhatsApp 运营/客服", "points": 30}}
        row.ad_count = 3
        await upsert_signal(
            s, lead_id, "whatsapp_number", "60123456789",
            source="website_enrich", evidence_url="https://sigtest.com/contact",
            confidence=95,
        )
        await s.commit()

    detail = (await client.get(f"/api/v1/collect/leads/{lead_id}", headers=headers)).json()["data"]
    assert detail["overseas_signals"]["currencies"] == ["USD"]
    assert "wa_ops" in detail["job_signals"]
    assert detail["ad_count"] == 3
    sig_list = detail["signals"]
    assert any(s["signal_type"] == "whatsapp_number" and s["value"] == "60123456789" for s in sig_list)
    assert any(s["signal_type_label"] for s in sig_list)
    assert _json.dumps(detail)  # 可序列化
