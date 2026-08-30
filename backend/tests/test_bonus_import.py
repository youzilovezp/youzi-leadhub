"""PRD 补课批测试：MVP 加分制明细 / 新信号检测（群链接·WA Business·竞品BSP）/ Seed Pool 导入。"""

import pytest

# ---------- §五 MVP 加分制明细 ----------


def test_bonus_breakdown_prd_signals():
    """PRD §五 13 条加分表的映射与计分（CTWA+40/官网WA+30/竞品SaaS+30…）。"""
    from app.collectors.scoring import bonus_breakdown

    bd = bonus_breakdown(
        fb_whatsapp=True,  # CTWA 代理 +40
        whatsapp_hit=True,  # 官网 WA +30
        whatsapp_numbers=["60111111", "60222222"],  # 多分线 +10
        wa_business=True,  # WA Business +15
        job_signals={"wa_ops": {"label": "x", "points": 30}},  # +30
        saas_signals={"wa_bsp": 2},  # 竞品 SaaS +30
        sources=[{"source": "meta_ads"}],  # +15
        is_cn=True,  # 海外业务 +15
        overseas_signals={"languages": ["en"], "ecommerce": ["shopify"]},  # 独立站 +10
        target_countries=["US", "GB", "AE", "SA"],  # ≥3 国 +10
        social={"facebook": "a", "instagram": "b", "youtube": "c"},  # 社媒 +5
    )
    pts = {it["key"]: it["points"] for it in bd["items"]}
    assert pts == {
        "ctwa_ad": 40, "site_whatsapp": 30, "wa_ops_job": 30, "wa_bsp_competitor": 30,
        "wa_business": 15, "meta_ads": 15, "overseas_biz": 15, "multi_numbers": 10,
        "overseas_site": 10, "three_markets": 10, "social_active": 5,
    }
    # 11 条全命中 = 200 原始分 → 封顶 100
    assert bd["total"] == 100


def test_bonus_breakdown_empty_and_cap():
    """无信号 → 空明细 0 分；全命中封顶 100。"""
    from app.collectors.scoring import bonus_breakdown

    empty = bonus_breakdown()
    assert empty == {"total": 0, "items": []}

    # 全命中（含 overseas_cs_job/crm_job）= 210 分原始 → 封顶 100
    full = bonus_breakdown(
        fb_whatsapp=True, whatsapp_hit=True, wa_business=True,
        whatsapp_numbers=["1", "2"],
        job_signals={"wa_ops": {}, "overseas_cs": {}, "crm_ops": {}},
        saas_signals={"wa_bsp": 1}, sources=[{"source": "meta_ads"}],
        is_cn=True, overseas_signals={"languages": ["en"]},
        target_countries=["US", "GB", "AE"], social={"a": "1", "b": "2", "c": "3"},
    )
    assert full["total"] == 100


def test_apply_score_writes_breakdown():
    """apply_score 同步写 score_breakdown（ORM 行级）。"""
    from app.collectors.scoring import apply_score

    class FakeLead:
        fb_whatsapp = True
        whatsapp_hit = True
        country = "CN"
        website = "https://x.com"
        whatsapp_url = None
        whatsapp_job = False
        whatsapp_numbers = ["6011"]
        wa_business = False
        scenes: list = []
        saas_signals: dict = {}
        job_urls: list = []
        social: dict = {}
        email = None
        phone_raw = None
        phone_e164 = None
        sources = [{"source": "meta_ads"}]
        is_cn = True
        overseas_signals: dict = {}
        job_signals: dict = {}
        ad_count = 0
        target_countries: list = []
        score = 0
        score_signals: dict = {}
        grade = "C"
        score_breakdown: dict = {}

    lead = FakeLead()
    apply_score(lead)
    pts = {it["key"]: it["points"] for it in lead.score_breakdown["items"]}
    assert pts == {"ctwa_ad": 40, "site_whatsapp": 30, "meta_ads": 15, "overseas_biz": 15}
    assert lead.score_breakdown["total"] == 100 - 0 or lead.score_breakdown["total"] == 100


# ---------- §4.1 新信号检测 ----------


def test_detect_whatsapp_groups_and_wa_business():
    """群邀请链接 + WhatsApp Business 自述检测。"""
    from app.collectors.website_enrich import detect_wa_business, detect_whatsapp_groups

    html = """
    <a href="https://chat.whatsapp.com/AbCd1234XyZ">Join our VIP group</a>
    <a href="https://chat.whatsapp.com/AbCd1234XyZ">重复链接只记一次</a>
    <p>Contact us on WhatsApp Business for bulk orders</p>
    """
    groups = detect_whatsapp_groups([html])
    assert groups == ["https://chat.whatsapp.com/AbCd1234XyZ"]
    assert detect_wa_business([html]) is True
    assert detect_wa_business(["<p>chat on whatsapp with us</p>"]) is False


def test_detect_saas_signals_wa_bsp():
    """BSP 竞品栈指纹（Wati/360dialog/Gupshup…）→ saas_signals.wa_bsp。"""
    from app.collectors.scenes import detect_saas_signals

    html = '<script src="https://app.wati.io/widget.js"></script><a href="https://360dialog.com">partner</a>'
    sig = detect_saas_signals([html])
    assert sig.get("wa_bsp", 0) >= 2

    clean = "<p>We sell shoes online</p>"
    assert "wa_bsp" not in detect_saas_signals([clean])


def test_detect_social_youtube():
    """YouTube 频道链接进社媒。"""
    from app.collectors.website_enrich import detect_social

    html = '<a href="https://www.youtube.com/@acmeofficial">YouTube</a>'
    assert "youtube" in detect_social([html])


# ---------- Seed Pool 导入（PRD 模块①） ----------


@pytest.mark.asyncio
async def test_seed_import_api(client, admin_credentials):
    """CSV 批量导入：建新/去重合并/缺名跳过，source=seed_import，is_cn 标记。"""
    login = await client.post("/api/v1/auth/login", json=admin_credentials)
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    csv_text = (
        "name,website,phone,country,city,industry\n"
        "深圳星河科技有限公司,https://xinghe-tech.com,+8675512345678,CN,深圳,跨境电商\n"
        "广州云帆游戏,https://yunfang.games,+862012345678,CN,广州,游戏\n"
        "缺网站公司,,+8613999900001,CN,,\n"
    )
    r = await client.post(
        "/api/v1/collect/leads/import", headers=headers, json={"csv_text": csv_text}
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["total"] == 3 and data["created"] == 3 and data["skipped"] == 0

    # 再导一次 → 全部合并（去重生效）
    r2 = await client.post(
        "/api/v1/collect/leads/import", headers=headers, json={"csv_text": csv_text}
    )
    d2 = r2.json()["data"]
    assert d2["created"] == 0 and d2["merged"] == 3

    # 列表可按 seed_import 源筛到，且 is_cn 已标记（中国企业种子）
    lst = (await client.get("/api/v1/collect/leads?is_cn=true&page_size=50", headers=headers)).json()["data"]
    names = {i["name"] for i in lst["items"]}
    assert "深圳星河科技有限公司" in names and "广州云帆游戏" in names

    # 缺名行跳过 + 错误信息
    r3 = await client.post(
        "/api/v1/collect/leads/import",
        headers=headers,
        json={"csv_text": "name\n\n,https://x.com\n好公司"},
    )
    d3 = r3.json()["data"]
    assert d3["skipped"] >= 1 and d3["created"] >= 1 and d3["errors"]

    # 空内容 → 业务错误
    r4 = await client.post(
        "/api/v1/collect/leads/import", headers=headers, json={"csv_text": "name\n"}
    )
    assert r4.status_code == 400 or r4.json()["code"] != 0
