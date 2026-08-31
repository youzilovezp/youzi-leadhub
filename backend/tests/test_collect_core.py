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


async def test_fetch_falls_back_to_direct_when_proxy_dead(monkeypatch):
    """双通道回归（原 P0「代理软拦截误报」的现行语义，2026-08-31 起）：
    主 client 代理优先，代理不可用时宽松兜底 client（trust_env=False 直连）
    仍能抓到页面——单一死代理不再造成误报。

    用本地回环 HTTP 服务验证，不依赖外部网络（此前 primal.com.ph / baidu
    直连路由抖动会让回归摇摆）。"""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _LocalHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b"<html>local-ok-dual-channel</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:  # 静默，别污染测试输出
            pass

    srv = HTTPServer(("127.0.0.1", 0), _LocalHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        # 模拟失效的系统代理：任何请求都打到不存在的本地端口
        monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:1")
        monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
        from app.collectors.website_enrich import _SSL_LOOSE_CLIENT_ARGS, _fetch_site, _make_client

        async with _make_client() as primary, _make_client(**_SSL_LOOSE_CLIENT_ARGS) as loose:
            html = await _fetch_site((primary, loose), f"http://127.0.0.1:{port}/")
        assert html is not None and "local-ok-dual-channel" in html
    finally:
        srv.shutdown()


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
    # whatsapp_job=True 必须伴随 wa_ops 信号（job_posting 生产者不变式；
    # 合并自愈会把无 wa_ops 证据的 True 重置——2026-08-31 验证轮修脏数据）
    d2 = LeadDraft(
        source="job_posting",
        name="ACME SDN. BHD.",
        country="MY",
        city="Kuala Lumpur",
        website="https://www.acme.com",
        whatsapp_url="https://wa.me/60123456789",
        whatsapp_job=True,
        job_signals={"wa_ops": {"label": "WhatsApp 运营/客服", "points": 30}},
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


def test_job_posting_jobui_parsing():
    """jobui 职位卡解析：公司名/职位名/链接提取 + 招聘信号按标题分类 + is_cn 标记。

    whatsapp_job 只认岗位标题本身含 WhatsApp 语义——搜索词仅是检索入口。
    """
    from app.collectors.job_posting import parse_jobui_html

    html = """
    <div class="c-job-list">
      <h3><a href="/job/315174730/">WhatsApp 海外客服专员</a></h3>
      <a href="/company/22833319/" class="job-company-logo-link"><img src="x.png"></a>
      <span class="job-company-name">上海派能能源科技股份有限公司</span>
      <span class="job-city">上海</span>
      <a href="/job/315174730/">查看</a>
    </div>
    <div class="c-job-list">
      <h3>行政前台</h3>
      <span class="job-company-name">某中国公司</span>
      <a href="/job/332284665/">查看</a>
    </div>
    """
    drafts = parse_jobui_html(html, "https://www.jobui.com/jobs?jobKw=whatsapp")
    assert len(drafts) == 2
    d1, d2 = drafts
    assert d1.name == "上海派能能源科技股份有限公司"
    assert d1.country == "CN" and d1.is_cn is True  # 中国招聘站 → 中国企业
    assert d1.city == "上海"
    assert d1.job_urls == ["https://www.jobui.com/job/315174730/"]
    assert d1.whatsapp_job is True  # 标题含 WhatsApp 海外客服
    assert "wa_ops" in d1.job_signals and "overseas_cs" in d1.job_signals
    assert d2.whatsapp_job is False and d2.job_signals == {}  # 普通岗不误标

async def test_source_filter_regression(client, admin_credentials):
    """source 筛选回归：JSON 列 cast(Text) 写法在 SQLAlchemy 2.x 下构造即抛
    TypeError（存量 bug——cast(func.text()) 非法），此前是无测试盲区。"""
    h = {"Authorization": f"Bearer {(await client.post('/api/v1/auth/login', json=admin_credentials)).json()['data']['access_token']}"}
    for name, web in (("SrcFilterA Co", "https://srcfiltera.com"), ("SrcFilterB Co", "https://srcfilterb.com")):
        await client.post("/api/v1/collect/leads", headers=h, json={"name": name, "country": "MY", "website": web})
    # 不带 source：两条都在
    all_items = (await client.get("/api/v1/collect/leads?keyword=SrcFilter", headers=h)).json()["data"]["items"]
    assert len(all_items) == 2
    # 带 source=manual：筛选不炸且命中（此前 500）
    r = await client.get("/api/v1/collect/leads?keyword=SrcFilter&source=manual", headers=h)
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]["items"]) == 2
    # 不存在的 source：空集
    r2 = await client.get("/api/v1/collect/leads?keyword=SrcFilter&source=web_search", headers=h)
    assert r2.status_code == 200 and r2.json()["data"]["items"] == []


def test_job_posting_liepin_parsing():
    """猎聘职位卡解析（2026-08-31 实测结构）：job-card-pc-container 锚点 +
    title 属性职位名 + company-info 公司名；匿名代发（某…公司）跳过；
    /a/（猎头）与 /job/（企业直发）两种详情链接都识别。"""
    from app.collectors.job_posting import parse_liepin_html

    html = (
        '<div class="_x job-card-pc-container" data-tlg-ext="%7B%22ckId%22%3A%22x%22%7D">'
        '<a data-nick="job-detail-job-info" href="https://www.liepin.com/a/77323779.shtml?pgRef=x&amp;d=1">'
        '<div class="_x ellipsis-1" title="招聘海外客服经理">招聘海外客服经理</div></a>'
        '<a data-nick="job-detail-company-info"><span class="_x ellipsis-1">海南诺尼生物工程开发有限公司</span></a></div>'
        '<div class="_x job-card-pc-container">'
        '<a data-nick="job-detail-job-info" href="https://www.liepin.com/job/1984803653.shtml?pgRef=x">'
        '<div class="_x ellipsis-1" title="WhatsApp 私域运营专员">WhatsApp 私域运营专员</div></a>'
        '<a data-nick="job-detail-company-info"><span class="_x ellipsis-1">某国内大型食品/饮料/酒水公司</span></a></div>'
    )
    drafts = parse_liepin_html(html, "https://www.liepin.com/zhaopin/?key=x")
    assert len(drafts) == 1  # 匿名代发跳过
    d = drafts[0]
    assert d.name == "海南诺尼生物工程开发有限公司"
    assert d.is_cn is True and d.country == "CN"
    assert d.job_urls == ["https://www.liepin.com/a/77323779.shtml"]  # 去 query
    assert "overseas_cs" in d.job_signals  # 海外客户服务经理
    assert d.whatsapp_job is False


def test_job_posting_51job_parsing():
    """51job 职位卡解析（2026-08-31 实测结构）：sensorsdata 结构化 JSON 取
    职位名/城市（jobArea · 前段），cname 取公司名。"""
    import html as _html
    import json as _json

    from app.collectors.job_posting import parse_51job_html

    sensors = _html.escape(_json.dumps(
        {"jobId": "173460250", "jobTitle": "海外销售代表（船司）", "jobArea": "上海·虹口区",
         "jobSalary": "1-1.5万", "companyId": "9750561"}
    ))
    html = f"""
    <div class="joblist-item"><div sensorsname="JobShortExposure" sensorsdata="{sensors}">
      <span class="jname text-cut">海外销售代表（船司）</span>
      <span class="cname text-cut"> 浙江海创运联网络科技有限公司 </span>
    </div></div>
    <div class="joblist-item"><div sensorsdata="{_html.escape(_json.dumps({'jobId': '1', 'jobTitle': '行政前台', 'jobArea': '北京·朝阳区'}))}">
      <span class="cname text-cut">某某公司</span>
    </div></div>
    """
    drafts = parse_51job_html(html, "https://we.51job.com/pc/search?keyword=x")
    assert len(drafts) == 2
    d1, d2 = drafts
    assert d1.name == "浙江海创运联网络科技有限公司"
    assert d1.city == "上海"  # jobArea · 前段
    assert "overseas_sales" in d1.job_signals or "overseas_cs" in d1.job_signals
    assert d2.job_signals == {}  # 行政前台不误标
    assert d1.job_urls == ["https://we.51job.com/pc/search?keyword=x"]  # SPA 无静态详情 href


def test_job_posting_stub_sites_rejected():
    """BOSS/智联实测被验证码拦截：选了明确报错带原因，不产出假成功。"""
    import pytest

    from app.collectors.job_posting import JobPostingCollector
    from app.core.exceptions import BusinessError

    c = JobPostingCollector()
    for site in ("zhipin", "zhilian"):
        with pytest.raises(BusinessError) as ei:
            c.validate_params({"site": site})
        assert "实测" in str(ei.value.message)
    c.validate_params({"site": "liepin"})  # 已适配站通过
    c.validate_params({})  # 缺省 jobui


def test_career_site_link_and_signal_extraction():
    """企业招聘官网巡检：首页找「招聘」链接（锚文本/URL 命中，外链 ATS 域放行）
    + 招聘页文本逐条过分类器（分类器即过滤器）。"""
    from app.collectors.career_site import extract_job_signals, find_career_link

    homepage = """
    <a href="/about">关于我们</a>
    <a href="https://app.mokahr.com/campus-apply/ugreen">加入我们</a>
    <a href="/products">产品中心</a>
    """
    url = find_career_link(homepage, "https://www.ugreen.com/", "ugreen.com")
    assert url == "https://app.mokahr.com/campus-apply/ugreen"  # ATS 外链放行

    assert find_career_link('<a href="/careers">Careers</a>', "https://a.com/", "a.com") == "https://a.com/careers"
    assert find_career_link('<a href="/news">新闻</a>', "https://a.com/", "a.com") is None

    career_page = """
    <a>海外客服专员（英语）</a><li>WhatsApp 私域运营</li><a>行政前台</a>
    <a>海外社媒运营（Facebook/TikTok）</a><div>公司简介文字不算岗位</div>
    """
    signals = extract_job_signals(career_page)
    assert "overseas_cs" in signals and "wa_ops" in signals and "social_ops" in signals
