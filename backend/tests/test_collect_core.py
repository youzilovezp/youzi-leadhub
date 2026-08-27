"""线索采集核心逻辑测试：评分 / 归一化 / 去重合并。"""

from app.collectors.base import LeadDraft
from app.collectors.normalize import (
    extract_domain,
    make_dedupe_key,
    normalize_company_name,
    normalize_phone,
)
from app.collectors.scoring import compute_score
from app.collectors.website_enrich import detect_email, detect_social, detect_whatsapp
from app.crud.lead import upsert_lead

# ---------- 评分 ----------


def test_score_full_hits():
    score, signals = compute_score(
        whatsapp_hit=True,
        whatsapp_job=True,
        website="https://a.com",
        email="x@a.com",
        country="MY",
        phone_raw="+6012345678",
        phone_e164="+6012345678",
        social={"facebook": "https://facebook.com/a"},
    )
    assert score == 110  # 文档口径：满分 110，不封顶
    assert set(signals) == {
        "whatsapp_plugin",
        "whatsapp_job",
        "has_website",
        "has_public_email",
        "is_target_region",
        "has_phone",
        "has_social",
    }


def test_score_non_target_country():
    score, signals = compute_score(
        whatsapp_hit=False,
        whatsapp_job=False,
        website=None,
        email=None,
        country="US",
        phone_raw=None,
        phone_e164=None,
        social=None,
    )
    assert score == 0
    assert signals == {}


# ---------- 电话归一化 ----------


def test_normalize_phone_with_region():
    assert normalize_phone("012-345 6789", "MY") == "+60123456789"
    assert normalize_phone("0917 123 4567", "PH") == "+639171234567"


def test_normalize_phone_international():
    assert normalize_phone("+65 6123 4567") == "+6561234567"


def test_normalize_phone_garbage():
    assert normalize_phone("not-a-phone") is None
    assert normalize_phone("") is None
    assert normalize_phone(None) is None


# ---------- 域名 ----------


def test_extract_domain():
    assert extract_domain("https://www.example.com.sg/about?utm=x") == "example.com.sg"
    assert extract_domain("http://blog.example.com") == "example.com"
    assert extract_domain("example.co.uk") == "example.co.uk"
    assert extract_domain("a@b.com") is None
    assert extract_domain(None) is None


# ---------- 公司名 ----------


def test_normalize_company_name():
    assert normalize_company_name("Acme Sdn. Bhd.") == "acme"
    assert normalize_company_name("PT Maju Jaya!") == "maju jaya"
    assert normalize_company_name("Foo PTE LTD") == "foo"


# ---------- dedupe_key 优先级 ----------


def test_dedupe_key_priority():
    assert make_dedupe_key(website="https://www.a.com", phone_raw="+60123456789") == "domain:a.com"
    key = make_dedupe_key(website=None, phone_raw="0123456789", region="MY")
    assert key == "tel:+60123456789"
    key = make_dedupe_key(name="Acme Sdn Bhd", city="Kuala Lumpur")
    assert key and key.startswith("namecity:")


# ---------- 富化检测 ----------


HTML = """
<a href="https://wa.me/60123456789">chat</a>
<a href="mailto:sales@acme.com">mail</a>
<a href="https://www.facebook.com/acme">fb</a>
<a href="https://t.me/acme">tg</a>
"""


def test_detect_whatsapp():
    hit, url = detect_whatsapp([HTML])
    assert hit and url == "https://wa.me/60123456789"


def test_detect_whatsapp_plugin_fingerprint():
    hit, url = detect_whatsapp(['<div class="ht-ctc-chat"></div>'])
    assert hit and url is None


def test_detect_email_and_social():
    assert detect_email(HTML) == "sales@acme.com"
    social = detect_social([HTML])
    assert social["facebook"].endswith("/acme")
    assert social["telegram"] == "https://t.me/acme"


def test_detect_email_rejects_asset_filenames():
    assert detect_email('<img src="ff-shopify-logo_250x@2x.png">') is None


def test_detect_whatsapp_negative():
    assert detect_whatsapp(["<html>nothing</html>"]) == (False, None)


# ---------- 富化抓取：client 必须无视系统代理（primal.com.ph 症状回归） ----------


async def test_fetch_ignores_env_proxy(monkeypatch):
    import httpx

    from app.collectors.website_enrich import _fetch

    # 模拟被劫持的系统代理：任何请求都打到不存在的本地端口
    monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:1")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    async with httpx.AsyncClient() as _:  # 确认 httpx 默认会受影响的环境存在
        pass
    from app.collectors.website_enrich import _make_client

    async with _make_client() as client:
        html = await _fetch(client, "https://www.primal.com.ph/")
    assert html is not None and "primal" in html.lower()


async def test_fetch_site_scheme_fallback():
    from app.collectors.website_enrich import _fetch_site, _make_client

    async with _make_client() as primary, _make_client(verify=False) as loose:
        # http:// 强制走一个 https-only 域名 → 主 client 失败，换 scheme 应成功
        html = await _fetch_site((primary, loose), "http://kalibrr.com/job-board")
    assert html is not None


# ---------- 去重合并（DB） ----------


async def test_upsert_merge(db_session):
    from app.db.init_db import init_db

    await init_db()  # db_session fixture 不建表，这里补
    d1 = LeadDraft(
        source="google_maps",
        name="Acme Sdn Bhd",
        country="MY",
        city="Kuala Lumpur",
        phone_raw="0123456789",
        website=None,
        industry="dental",
    )
    lead1, created1 = await upsert_lead(db_session, d1)
    await db_session.commit()
    assert created1
    assert lead1.phone_e164 == "+60123456789"
    assert lead1.sources[0]["source"] == "google_maps"
    assert lead1.score == 15  # 目标地区10 + 电话5

    # 同一企业 second source：有官网 + WhatsApp 链接（同城 → namecity 反查命中）
    d2 = LeadDraft(
        source="job_posting",
        name="ACME SDN. BHD.",
        country="MY",
        city="Kuala Lumpur",
        website="https://www.acme.com",
        whatsapp_url="https://wa.me/60123456789",
        whatsapp_job=True,
        job_urls=["https://kalibrr.com/c/acme/jobs/1/x"],
    )
    lead2, created2 = await upsert_lead(db_session, d2)
    await db_session.commit()
    assert not created2 and lead2.id == lead1.id
    # 补空：website/domain；OR：whatsapp_hit/job
    assert lead2.website == "https://www.acme.com"
    assert lead2.domain == "acme.com"
    assert lead2.whatsapp_hit and lead2.whatsapp_job
    assert lead2.job_urls and lead2.job_urls[0].startswith("https://kalibrr.com")
    # 来源记录按 (lead, source) 唯一：两个来源，无重复
    assert sorted(s["source"] for s in lead2.sources) == ["google_maps", "job_posting"]

    # 评分重算：plugin40+job30+website10+region10+phone5（无邮箱/社媒）=95
    assert lead2.score == 95

    # 第三次同 source 不追加来源记录
    d3 = LeadDraft(source="google_maps", name="Acme", country="MY", website="https://acme.com")
    lead3, created3 = await upsert_lead(db_session, d3)
    await db_session.commit()
    assert not created3
    assert len(lead3.sources) == 2


async def test_upsert_skips_empty_name(db_session):
    from app.db.init_db import init_db

    await init_db()
    import pytest

    with pytest.raises(ValueError):
        await upsert_lead(db_session, LeadDraft(source="manual", name="  "))
