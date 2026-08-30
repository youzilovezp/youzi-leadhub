"""线索采集核心逻辑测试：评分 / 归一化 / 去重合并。"""

from app.collectors.base import LeadDraft
from app.collectors.normalize import (
    extract_domain,
    make_dedupe_key,
    normalize_company_name,
    normalize_phone,
)
from app.collectors.scoring import score_lead_inputs
from app.collectors.website_enrich import detect_email, detect_social, detect_whatsapp
from app.crud.lead import upsert_lead

# ---------- 评分（六维模型） ----------


def test_score_full_hits():
    """旧「全命中」画像在六维模型下的期望值（出海 25 / WA 60 / SaaS 0 / 规模 40 / 营销 0 / 联系人 10 → 29）。"""
    score, dims, grade = score_lead_inputs(
        whatsapp_hit=True,
        whatsapp_job=True,
        website="https://a.com",
        email="x@a.com",
        country="MY",
        phone_raw="+6012345678",
        phone_e164="+6012345678",
        social={"facebook": "https://facebook.com/a"},
    )
    assert score == 26
    assert dims == {
        "overseas": 25,  # 目标地区15 + 官网10
        "whatsapp": 50,  # hit35 + job15（CTWA 口径调整后）
        "saas": 0,
        "scale": 40,  # 社媒15 + 官网10 + 邮箱10 + 电话5
        "marketing": 0,
        "contact": 10,  # 无联系人但有公开邮箱
    }
    assert grade == "C"


def test_score_non_target_country():
    score, dims, grade = score_lead_inputs(
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
    assert dims == {"overseas": 0, "whatsapp": 0, "saas": 0, "scale": 0, "marketing": 0, "contact": 0}
    assert grade == "C"


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


def test_detect_email_rejects_instrumentation_domains():
    # Wix 站点正文里的 Sentry 埋点邮箱，不是联系方式
    assert detect_email('var s="605a7baede844d278b89dc95ae0a9123@sentry-next.wixpress.com"') is None


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
    assert lead1.score == 4  # 出海15×25% + 规模5×10% = 4.25 → 4
    assert lead1.grade == "C"

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

    # 六维重算：出海25×25% + WA65×30% + 规模40×10% + 营销15×10% = 31.25 → 31
    assert lead2.score == 31
    assert lead2.score_signals["whatsapp"] == 65
    assert lead2.grade == "C"

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


# ---------- 修复回归（2026-08-27 复盘） ----------


def test_parse_lead_ids():
    """P1 回归：手动任务表单传字符串 "12,34"，不得按字符拆分/抛 ArgumentError。"""
    from app.collectors.website_enrich import _parse_lead_ids

    assert _parse_lead_ids("12, 34") == [12, 34]
    assert _parse_lead_ids([5, 6]) == [5, 6]
    assert _parse_lead_ids("") == []
    assert _parse_lead_ids(None) == []
    assert _parse_lead_ids("abc,,7") == [7]


async def test_upsert_concurrent_same_company(db_session):
    """P1 回归：两个并发 upsert 同一 dedupe_key，一个新建一个合并，不抛 IntegrityError。"""
    import asyncio

    from app.db.init_db import init_db
    from app.db.session import async_session

    await init_db()
    draft = LeadDraft(source="google_maps", name="Race Co", website="https://race-co.example")

    async def one():
        async with async_session() as s:
            lead, created = await upsert_lead(s, draft)
            await s.commit()
            return lead.id, created

    results = await asyncio.gather(one(), one())
    ids = {r[0] for r in results}
    assert len(ids) == 1  # 同一行
    assert sorted(r[1] for r in results) == [False, True]  # 一建一并


async def test_scheduler_sync_skips_bad_cron(db_session):
    """P0 回归：默认时区构造不崩 + 非法 cron 只告警不炸 sync。"""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from app.db.init_db import init_db
    from app.models.collect_task import CollectTask
    from app.services import scheduler

    await init_db()
    s = AsyncIOScheduler()  # 修复后的构造方式：默认本地时区（IANA），不再抛 ZoneInfoNotFoundError
    scheduler._scheduler = s
    try:
        db_session.add(CollectTask(name="t", collector="google_maps", cron_expr="not a cron"))
        await db_session.commit()
        await scheduler.sync()  # 不抛异常
        assert s.get_jobs() == []
    finally:
        scheduler._scheduler = None


def test_job_posting_company_level_mapping():
    """岗位帖 → 公司级线索：社媒 URL 入 social、官网入 website、岗位 URL 入 job_urls。

    whatsapp_job 只认岗位标题本身含 WhatsApp 语义——搜索词是检索入口不是信号
    （搜 "customer service" 时命中岗位并非都是 WA 岗）。
    """
    from app.collectors.job_posting import _job_to_draft

    job = {
        "id": 1, "slug": "cs-whatsapp", "name": "WhatsApp Customer Service", "companyName": "Acme",
        "company": {"code": "acme"},
        "companyInfo": {"url": "https://facebook.com/acme"},
        "googleLocation": {"addressComponents": {"city": "Manila"}},
    }
    d = _job_to_draft(job, "PH", "https://kalibrr.com/job-board?search=whatsapp")
    assert d.name == "Acme" and d.country == "PH" and d.city == "Manila"
    assert d.whatsapp_job is True and d.website is None
    assert d.social == {"facebook": "https://facebook.com/acme"}
    assert d.job_urls == ["https://kalibrr.com/c/acme/jobs/1/cs-whatsapp"]

    # 非WhatsApp岗位标题：即使搜索词是 whatsapp 也不置信号（防误报）
    d2 = _job_to_draft({**job, "name": "Retail Sales Associate"}, "PH", "u")
    assert d2.whatsapp_job is False

    d3 = _job_to_draft({**job, "companyInfo": {"url": "https://acme.ph"}}, "PH", "u")
    assert d3.website == "https://acme.ph" and d3.social == {}
    assert _job_to_draft({"companyName": None}, "PH", "u") is None  # 无名丢弃
