"""富化完整性修复测试（2026-08-31 审计批次1）。

- 邮箱检测扫全部页面（首页+联系/关于页），mailto 跨页优先
- 内页发现：中文锚文本（联系我们/产品）与英文 href 一并覆盖 + 同域过滤
- 证据链词表补 domain_tld
"""

from app.collectors.website_enrich import (
    detect_email,
    detect_tel_phones,
    detect_text_phones,
    detect_whatsapp,
    detect_whatsapp_numbers,
    find_inner_page_urls,
)

_HOME = "<html><body><h1>Acme Corp</h1><p>Welcome to our site.</p></body></html>"
_CONTACT = (
    "<html><body><h1>Contact Us</h1>" "<p>Email: sales@acme.com or visit our FAQ.</p></body></html>"
)


def test_detect_email_scans_all_pages():
    """首页无邮箱、联系页有 → 命中（审计前只扫首页，联系页邮箱全漏）。"""
    assert detect_email([_HOME, _CONTACT]) == "sales@acme.com"
    # 单页输入兼容（meta_ads 主页探测仍传单页）
    assert detect_email(_CONTACT) == "sales@acme.com"
    assert detect_email([_HOME]) is None


def test_detect_email_mailto_priority_across_pages():
    """mailto 链接优先于正文正则，且跨页先扫完 mailto。"""
    home = '<a href="mailto:info@acme.com">Mail</a>'
    contact = "<p>write to sales@acme.com</p>"
    assert detect_email([home, contact]) == "info@acme.com"
    assert detect_email([contact, home]) == "info@acme.com"


def test_find_inner_page_urls_chinese_anchor_and_domain_filter():
    """中文锚文本（href 无英文关键词）也命中；跨域链接不跟；上限 3。"""
    home = """
    <a href="/p/10086">联系我们</a>
    <a href="/about-us">About Us</a>
    <a href="/goods/list">产品中心</a>
    <a href="https://facebook.com/acme">我们的 Facebook</a>
    <a href="/shop/1">Store</a>
    <a href="/shop/2">Shop 2</a>
    """
    urls = find_inner_page_urls(home, "https://acme.com", "acme.com")
    assert "https://acme.com/p/10086" in urls  # 中文锚文本命中
    assert "https://acme.com/about-us" in urls  # 英文 href 命中
    assert "https://acme.com/goods/list" in urls  # 「产品」命中
    assert not any("facebook.com" in u for u in urls)  # 跨域不跟
    assert len(urls) == 3  # 上限 _MAX_INNER_PAGES


def test_signal_label_covers_domain_tld():
    """domain_tld 信号有中文标签（审计前前端证据卡显示原始 key）。"""
    from app.crud.lead_signals import SIGNAL_TYPE_LABELS_ZH

    assert SIGNAL_TYPE_LABELS_ZH.get("domain_tld") == "海外域名"


def test_detect_whatsapp_web_domain_send_link():
    """web.whatsapp.com/send?phone= 形态（2026-09-01 实测 mugroup.com 漏检根因：
    人工放的「通过 WhatsApp 分享」链接是 web. 子域，旧正则只认 api/wp/wa.me，
    结果 whatsapp_hit=True 却拿不到号码——wa_url/号码/自动联系人全空）。"""
    html = '<a href="https://web.whatsapp.com/send?phone=8613736028159">WhatsApp</a>'
    hit, url = detect_whatsapp([html])
    assert hit is True
    assert url == "https://wa.me/8613736028159"
    assert detect_whatsapp_numbers([html]) == ["8613736028159"]


def test_detect_text_phones_international_format():
    """明文国际电话（2026-09-01 实测 mugroup.com：「CONTACT US +86 137 3602 8159」，
    多数联系页不写 tel: 链接，电话就是正文文本；裸座机号不碰——只认 +区号前缀）。"""
    html = "<html><body>CONTACT US +86 137 3602 8159 marketing@mu.com</body></html>"
    # 国际号码与手机形态同串命中时只留国际形态（span 去重，不重复产出）
    assert detect_text_phones([html]) == ["+86 137 3602 8159"]
    # 座机/400 现在就是要抓（2026-09-01 用户需求：连最基础的电话都没有）
    # ——合法性校验交给 phonenumbers region=CN（enrich 调用侧），形态层宽抓
    assert "0755-12345678" in detect_text_phones(["<p>0755-12345678</p>"])
    assert "400-820-8820" in detect_text_phones(["<p>400-820-8820</p>"])
    # 纯数字串（订单号等）仍不产出：无前导 0/+/400 形态不匹配
    assert detect_text_phones(["<p>订单号 12345678901</p>"]) == []
    assert detect_text_phones(["<p>价格 +86 元起</p>"]) == []


def test_detect_tel_phones_still_works():
    assert detect_tel_phones(['<a href="tel:+8613736028159">Call</a>'])


def test_inner_pages_prioritize_contact_over_product():
    """F3b（2026-09-01 TMO 实测）：产品/服务链接在导航里先于「联系我们」出现，
    旧逻辑 3 页上限先被产品页占满 → /contact/ 被丢 → 电话全漏。
    联系页（联系方式是富化第一产出）必须优先于产品页入选。"""
    from app.collectors.website_enrich import find_inner_page_urls

    home = """
    <nav>
      <a href="/services/#product_registration">产品信息备案</a>
      <a href="/services/#product_enrichment">产品信息优化</a>
      <a href="/services/shopify/">Shopify开发</a>
      <a href="/contact/">联系我们</a>
    </nav>
    """
    inner = find_inner_page_urls(home, "https://tmotest.com/", "tmotest.com")
    assert "https://tmotest.com/contact/" in inner
    assert inner[0].endswith("/contact/")  # 联系页排第一
    assert len(inner) <= 3


def test_inner_pages_www_variant_dedup():
    """www 变体归一去重（2026-09-01 mugroup 探针实证）：首页同挂
    www.x.com/who-we-are/ 与 x.com/who-we-are/ 两个写法——同页不得占两个
    内页名额（3 页上限会被白烧，挤掉真正的第三页）。"""
    from app.collectors.website_enrich import find_inner_page_urls

    home = """
    <nav>
      <a href="https://www.deduptest.com/who-we-are/">About</a>
      <a href="https://deduptest.com/who-we-are/">About</a>
      <a href="/contact/">Contact</a>
      <a href="/products/">Products</a>
    </nav>
    """
    inner = find_inner_page_urls(home, "https://deduptest.com/", "deduptest.com")
    keys = {u.split("deduptest.com")[-1].rstrip("/") for u in inner}
    assert len(keys) == len(inner), f"www 变体重复入选: {inner}"
    assert len(inner) == 3  # who-we-are(一席) + contact + products


def test_wildcard_page_urls_for_archaic_sites():
    """老式站兜底（2026-09-01 laifen 实测）：联系信息挂在 asp-bin/GB/?page=1
    这类无关键词 query 页，词表与惯例路径全够不着——取首页同域普通链接。"""
    from app.collectors.website_enrich import find_wildcard_page_urls

    home = """
    <a href="asp-bin/GB/gb2312.css">css</a>
    <a href="asp-bin/GB/?page=1">中文版</a>
    <a href="asp-bin/EN/?page=1">EN</a>
    <a href="https://friend-link.cn/">友情链接</a>
    """
    urls = find_wildcard_page_urls(home, "http://wildtest.com/", "wildtest.com")
    assert len(urls) == 2  # 同域 query 页 2 个；css 静态资源与外域友情链接排除
    assert all("wildtest.com" in u for u in urls)
    assert all(not u.endswith(".css") for u in urls)


def test_detect_jsonld_contacts_schema_org():
    """JSON-LD 声明即权威（2026-09-01）：网站主写的机器可读联系方式，
    命中优先于正则启发。注：TMO/mugroup 实测页面无联系字段——本通道
    覆盖的是「声明了」的站点，零依赖借 schema.org 标准。"""
    from app.collectors.website_enrich import detect_jsonld_contacts

    html = """
    <script type="application/ld+json">
    {"@type": "Organization", "name": "Acme",
     "telephone": "+86-21-1234-5678", "email": "sales@acme.com",
     "address": {"streetAddress": "1107 Guangfu West Rd", "addressLocality": "Shanghai",
                  "addressCountry": "CN"}}
    </script>
    """
    got = detect_jsonld_contacts([html])
    assert got["phone"] == "+86-21-1234-5678"
    assert got["email"] == "sales@acme.com"
    assert "Guangfu West Rd" in got["address"]
    assert detect_jsonld_contacts(["<p>无结构化数据</p>"]) == {}


def test_site_matches_company_distractor_rule():
    """官网归属校验（2026-09-01 实测三类错配）：标题=知名平台且与公司名零重叠
    → 判错配；字面零重叠但无平台干扰词（凯越/MU Group）→ 存疑放行不误杀。"""
    from app.collectors.website_enrich import site_matches_company

    # 实测错配形态
    ok, title = site_matches_company("<title>酷狗音乐 - 就是歌多！</title>", "酷集科技")
    assert ok is False and "酷狗" in title
    ok2, _ = site_matches_company(
        "<title>QQ邮箱电脑版_网页端免费邮件服务</title>", "艾普锐智能装备（嘉兴）有限公司"
    )
    assert ok2 is False
    ok3, _ = site_matches_company("<title>汉典</title>", "宸星体育用品（上海）有限责任公司")
    assert ok3 is False
    # 名-站一致 / 字面零重叠但非平台（存疑放行） / 无标题（放行）
    assert site_matches_company("<title>凯迪仕智能锁-智能指纹锁</title>", "凯迪仕")[0] is True
    assert (
        site_matches_company("<title>MU Group: China's first supply chain</title>", "宁波凯越集团")[
            0
        ]
        is True
    )
    assert site_matches_company("<html>no title</html>", "任意公司")[0] is True


async def test_clear_mismatched_website_purges_wrong_site_data(db_session):
    """错配清除要连坐：错站抓的邮箱/电话/WA/信号/证据链/自动联系人全清（全是别人的数据）。"""
    from sqlalchemy import select

    from app.collectors.base import LeadDraft
    from app.collectors.website_enrich import _clear_mismatched_website
    from app.crud.contact import auto_create_from_email
    from app.crud.lead import upsert_lead
    from app.db.init_db import init_db
    from app.models.lead import Lead, LeadContact, LeadSignal

    await init_db()  # 单文件跑时 db_session 不经 client fixture，需自建表

    lead, _ = await upsert_lead(
        db_session,
        LeadDraft(
            source="web_search",
            name="错配清除测试科技（杭州）有限公司",
            website="https://kugou.com",
            is_cn=True,
            email="wrong@kugou.com",
        ),
    )
    await db_session.flush()
    await auto_create_from_email(db_session, lead, "wrong@kugou.com", source="website_enrich")
    db_session.add(
        LeadSignal(
            lead_id=lead.id,
            signal_type="whatsapp_link",
            value="https://wa.me/8613800000000",
            source="website_enrich",
            evidence_url="https://kugou.com",
        )
    )
    await db_session.commit()
    assert lead.email and lead.website
    assert lead.dedupe_key == "domain:kugou.com"

    lead_id = lead.id  # expire 前缓存：expire 后访问 lead.id 触发懒加载
    await _clear_mismatched_website(lead_id, "https://kugou.com", "酷狗音乐", session=db_session)
    await db_session.commit()
    db_session.expire_all()
    got = await db_session.get(Lead, lead_id)
    await db_session.refresh(got)  # commit 后属性访问会触发懒加载 MissingGreenlet
    assert got.website is None and got.domain is None
    assert got.email is None and got.phone_e164 is None
    assert not got.whatsapp_hit and got.saas_signals == {} and got.overseas_signals == {}
    left = (
        (await db_session.execute(select(LeadContact).where(LeadContact.lead_id == got.id)))
        .scalars()
        .all()
    )
    assert left == []  # draft 落库建的联系人（source=web_search）也必须连坐删除
    # 信号证据链同样连坐清空（详情页证据卡不能残留错站来源）
    sigs = (
        (await db_session.execute(select(LeadSignal).where(LeadSignal.lead_id == got.id)))
        .scalars()
        .all()
    )
    assert sigs == []
    # 身份键回退无官网状态：不再是 domain:（否则官网发现找到真身时会反向并入错配行）
    assert got.dedupe_key != "domain:kugou.com"
    assert got.dedupe_key.startswith("namecity:")
    assert got.field_meta["website"]["source"] == "mismatch_clear"
    await _delete_tree(db_session, got.id)


async def test_clear_mismatch_keeps_meta_ads_evidence(db_session):
    """错配清除不误杀 meta_ads 证据（2026-09-01 审计）：FB 主页探测的 WA/
    信号/联系人是 FB 维度数据、与错站无关——清了会丢 CTWA(+40) 并可致 ICP
    出海证据降级（qualified→cn_domestic），错杀最高价值形态。"""
    from sqlalchemy import select

    from app.collectors.base import LeadDraft
    from app.collectors.website_enrich import _clear_mismatched_website
    from app.crud.contact import auto_create_from_phone
    from app.crud.lead import upsert_lead
    from app.db.init_db import init_db
    from app.models.lead import Lead, LeadContact, LeadSignal

    await init_db()

    lead, _ = await upsert_lead(
        db_session,
        LeadDraft(
            source="meta_ads",
            name="主页证据保留测试（深圳）有限公司",
            website="https://zdic.net",
            is_cn=True,
            whatsapp_url="https://wa.me/8613911112222",
        ),
    )
    await db_session.flush()
    # FB 主页探测产物：WA 联系人 + fb_whatsapp 信号（field_meta 来源=meta_ads）
    await auto_create_from_phone(db_session, lead, "+8613911112222", source="meta_ads")
    db_session.add(
        LeadSignal(
            lead_id=lead.id,
            signal_type="fb_whatsapp",
            value="wa.me/8613911112222",
            source="meta_ads",
        )
    )
    lead.fb_whatsapp = True
    lead.field_meta = dict(lead.field_meta or {}, whatsapp_url={"source": "meta_ads"})
    await db_session.commit()
    lead_id = lead.id

    await _clear_mismatched_website(lead_id, "https://zdic.net", "汉典", session=db_session)
    await db_session.commit()
    db_session.expire_all()
    got = await db_session.get(Lead, lead_id)
    await db_session.refresh(got)
    # 错站数据照清
    assert got.website is None and got.domain is None
    # FB 维度证据保留
    assert got.fb_whatsapp is True
    assert got.whatsapp_hit is True
    assert got.whatsapp_url == "https://wa.me/8613911112222"
    contacts = (
        (await db_session.execute(select(LeadContact).where(LeadContact.lead_id == lead_id)))
        .scalars()
        .all()
    )
    assert any(c.source == "meta_ads" for c in contacts)
    sigs = (
        (await db_session.execute(select(LeadSignal).where(LeadSignal.lead_id == lead_id)))
        .scalars()
        .all()
    )
    assert any(s.source == "meta_ads" for s in sigs)
    await _delete_tree(db_session, got.id)


async def _delete_tree(db_session, lead_id):
    from sqlalchemy import delete

    from app.models.lead import Lead, LeadContact, LeadEvent, LeadFollowUp, LeadSignal

    for m in (LeadContact, LeadSignal, LeadEvent, LeadFollowUp):
        await db_session.execute(delete(m).where(m.lead_id == lead_id))
    await db_session.execute(delete(Lead).where(Lead.id == lead_id))
    await db_session.commit()


def test_detect_contact_persons_kaadas_shape():
    """具名联系人提取（2026-09-01 kaadas 联系页实测形态）：部门+人名+手机号。"""
    from app.collectors.website_enrich import detect_contact_persons

    text = """
    海外事业部（加盟合作/OEM/ODM） 联系人：屈先生 联系电话：189-2522-1831
    工程集采业务部(地产、智慧公寓&高校、园区等) 联系人：欧阳先生（负责人） 联系电话：136-0259-5599
    KA渠道（苏宁、国美、商超等） 联系人：李明祥 联系电话：153-6731-9213
    """
    persons = detect_contact_persons([f"<html><body>{text}</body></html>"])
    by_name = {p["name"]: p for p in persons}
    assert by_name["屈先生"]["phone"] == "18925221831"
    assert "海外事业部" in by_name["屈先生"]["title"]
    assert by_name["欧阳先生"]["phone"] == "13602595599"
    assert "负责人" in by_name["欧阳先生"]["title"]
    assert len(persons) == 3


def test_phone_rank_prefers_landline_over_400():
    """电话选取定序（kaadas 教训）：总机座机 > 国际 > 400 热线 > 手机。"""
    from app.collectors.website_enrich import detect_text_phones

    html = "<p>售后热线：400-800-5919 招商热线：400-800-3756 联系电话：0755-86668868</p>"
    phones = detect_text_phones([html])
    assert "0755-86668868" in phones and "400-800-5919" in phones


def test_pick_best_phone_rejects_jsonld_placeholder():
    """模板占位电话排除（2026-09-01 t-shinebakeware 实测：JSON-LD 声明
    +86-513-88888888（占位），真电话是 tel: 链接的 +8617368160555——phonenumbers
    会放过占位号，必须显式剔除；剔除后 JSON-LD 序位优势被真电话接替）。"""
    from app.collectors.website_enrich import is_placeholder_phone, pick_best_phone

    assert is_placeholder_phone("+86-513-88888888") is True
    assert is_placeholder_phone("88888888") is True
    assert is_placeholder_phone("666666666") is True
    assert is_placeholder_phone("+86-755-86668868") is False  # 4 连串不是占位
    assert is_placeholder_phone("+8617368160555") is False
    assert is_placeholder_phone("") is False

    # JSON-LD 占位 + tel 真号（候选顺序 jsonld 在前）→ 真号胜出
    best = pick_best_phone(
        ["+86-513-88888888", "+8617368160555"],
        current_raw=None,
        current_e164=None,
        region="CN",
    )
    assert best == ("+8617368160555", "+8617368160555")

    # 存量是占位号 → 同序位真号可替换（此前「仅更优序位才换」把错选锁死）
    best = pick_best_phone(
        ["+8617368160555"],
        current_raw="+86-513-88888888",
        current_e164="+8651388888888",
        region="CN",
    )
    assert best is not None and best[1] == "+8617368160555"

    # 存量是合法国际号 → 同序位候选不替换（不抖动）
    assert (
        pick_best_phone(
            ["+8617368160555"],
            current_raw="+86 137 3602 8159",
            current_e164="+8613736028159",
            region="CN",
        )
        is None
    )


def test_detect_jsonld_contacts_t_shine_shape():
    """JSON-LD 城市/组织名提取（2026-09-01 t-shinebakeware 实测：city 全空、
    公司名全是英文——JSON-LD 里有 addressLocality=南通市 与 LocalBusiness
    name=江苏台烁烘焙器具有限公司，此前 detect_jsonld_contacts 只收
    phone/email/address，两样关键基础信息被丢）。"""
    from app.collectors.website_enrich import detect_jsonld_contacts

    html = """
    <script type="application/ld+json">
    {"@type": "WebSite", "name": null, "url": "https://t-shinebakeware.com/"}
    </script>
    <script type="application/ld+json">
    {"@type": "LocalBusiness", "name": "江苏台烁烘焙器具有限公司",
     "alternateName": "T-Shine Bakeware",
     "telephone": "+86-513-88888888",
     "address": {"addressLocality": "南通市", "addressRegion": "江苏省",
                 "addressCountry": "CN"},
     "url": "https://t-shinebakeware.com/"}
    </script>
    """
    got = detect_jsonld_contacts([html])
    assert got["city"] == "南通市"
    assert got["country"] == "CN"
    assert got["org_name"] == "江苏台烁烘焙器具有限公司"
    assert got["phone"] == "+86-513-88888888"

    # WebSite 块的 name 不是公司名，不采信
    got2 = detect_jsonld_contacts(
        ['<script type="application/ld+json">{"@type": "WebSite", "name": "台烁官网"}</script>']
    )
    assert got2.get("org_name") is None


def test_extract_city_from_address():
    """地址文本 → 城市兜底（2026-09-01 重测：多数工厂站无 JSON-LD，city 全空
    ——地址行里三种形态现成可提：中文省XX市/XX市、英文「Qingdao City」、
    「Shijiazhuang, Hebei, China」）。"""
    from app.collectors.website_enrich import extract_city_from_address

    assert extract_city_from_address("广东省佛山市顺德区勒流街道263号") == "佛山市"
    assert extract_city_from_address("深圳市南山区科技园8栋") == "深圳市"
    assert extract_city_from_address("北京市朝阳区建国路88号") == "北京市"
    assert (
        extract_city_from_address("Xingyang Road, Chengyang District Qingdao City, Shandong")
        == "Qingdao"
    )
    assert extract_city_from_address("Shijiazhuang, Hebei, China") == "Shijiazhuang"
    assert extract_city_from_address("Group 28, Puxi Community, Baipu Town, Rugao City") == "Rugao"
    # 无城市形态不硬猜
    assert extract_city_from_address("West of Nanhao Village, Longhua Town") is None
    assert extract_city_from_address(None) is None
    assert extract_city_from_address("") is None


def test_detect_contact_persons_name_tel_shape():
    """具名联系人第 5 形态（2026-09-01 Shoptop 实测，用户报「联系信息爬取不对」）：
    「城市 Name Tel：手机 微信同号」——无「联系人：」前缀、拉丁名、微信同号标注。
    此前只抓到 400 热线，页面 5 位地区联系全漏。"""
    from app.collectors.website_enrich import detect_contact_persons

    text = """
    致电我们 400-888-3299
    上海 Lisa Tel：17891981788 微信同号
    上海 Hope Tel：13122987879 微信同号
    合肥 Akon Tel：13127786691 微信同号
    深圳/广州 Kay Tel：13122863363 微信同号
    郑州 Terry Tel：18616332393 微信同号
    """
    persons = detect_contact_persons([f"<html><body>{text}</body></html>"])
    by_name = {p["name"]: p for p in persons}
    assert len(persons) == 5
    assert by_name["Lisa"]["phone"] == "17891981788"
    assert "微信同号" in by_name["Lisa"]["title"]
    assert by_name["Akon"]["phone"] == "13127786691"
    assert "合肥" in by_name["Akon"]["title"]
    assert "深圳/广州" in by_name["Kay"]["title"]


def test_detect_contact_persons_name_tel_cn_name_and_junk_guard():
    """第 5 形态边界：中文名+带分隔手机可识别；「客服电话：」类非人名词不建联系人。"""
    from app.collectors.website_enrich import detect_contact_persons

    text = """
    业务咨询 李伟 电话：138-1234-5678
    客服电话：13900000000
    售后服务 mobile: 13711112222
    """
    persons = detect_contact_persons([f"<html><body>{text}</body></html>"])
    by_name = {p["name"]: p for p in persons}
    assert by_name.get("李伟", {}).get("phone") == "13812345678"
    # 客服/售后服务是部门职能词不是人名——不产出具名联系人
    assert "客服" not in by_name and "售后服务" not in by_name


def test_detect_contact_persons_dept_surname_title_shape():
    """第 5 形态修复（2026-09-01 富化层审计实测复现）：「部门+姓+职务」连写
    （商务部张经理 电话：…）——贪婪 ctx 把姓吃进部门、name 落到裸职务词，
    产出名为「经理」的假联系人。修复后从 ctx 尾取回姓；裸职务+手机（无部门）
    不产假联系人。"""
    from app.collectors.website_enrich import detect_contact_persons

    text = """
    商务部张经理 电话：138-1234-5678
    销售张总监 联系电话：139-1111-2222
    海外事业部王主管 手机：137-3333-4444
    经理 电话：136-5555-6666
    """
    persons = detect_contact_persons([f"<html><body>{text}</body></html>"])
    by_name = {p["name"]: p for p in persons}
    assert by_name.get("张经理", {}).get("phone") == "13812345678"
    assert "商务部" in by_name.get("张经理", {}).get("title", "")
    assert by_name.get("张总监", {}).get("phone") == "13911112222"
    assert by_name.get("王主管", {}).get("phone") == "13733334444"
    assert "海外事业部" in by_name.get("王主管", {}).get("title", "")
    # 无部门的裸职务词不是人名
    assert "经理" not in by_name


def test_site_matches_company_short_brand_identity():
    """归属 v2（呜噜网实测）：标题=短中文品牌（≤6字）且与公司名零重叠 → 错配；
    长口号标题/英文品牌站不触发（防误杀）。"""
    from app.collectors.website_enrich import site_matches_company

    assert site_matches_company("<title>呜噜网</title>", "上海冠天国际贸易有限公司")[0] is False
    assert site_matches_company("<title>汉典</title>", "宸星体育用品公司")[0] is False
    # 不触发：英文品牌站（零重叠但合法）、长口号标题、名-站一致
    assert (
        site_matches_company("<title>MU Group: China's first supply</title>", "宁波凯越集团")[0]
        is True
    )
    assert (
        site_matches_company("<title>高端全屋五金系统解决方案专家_国际一线</title>", "东泰五金")[0]
        is True
    )
    assert site_matches_company("<title>凯迪仕智能锁</title>", "凯迪仕")[0] is True


async def test_probe_export_en_pages_finds_wa(monkeypatch):
    """外销站英文页探测（2026-09-01 靶心补强）：国内站无 WA → en. 子域联系页命中。

    只探零错配风险形态（en.子域/同域 /en/），页面须含联系方式才收。
    """
    from app.collectors import website_enrich as we

    en_page = '<html><a href="https://wa.me/8613800138000">WhatsApp</a></html>'

    async def fake_fetch_site(clients, url):
        return en_page if url == "https://en.demo-factory.cn/" else None

    monkeypatch.setattr(we, "_fetch_site", fake_fetch_site)
    got = await we._probe_export_en_pages((), "https://www.demo-factory.cn/", "demo-factory.cn")
    assert len(got) == 1 and got[0][1] == "https://en.demo-factory.cn/"
    assert we.detect_whatsapp([got[0][0]])[0] is True

    # 页面无任何联系方式 → 不收（不浪费检测预算）

    async def fake_fetch_empty(clients, url):
        return "<html><h1>Home</h1>nav only</html>"

    monkeypatch.setattr(we, "_fetch_site", fake_fetch_empty)
    assert await we._probe_export_en_pages((), "https://www.x.cn/", "x.cn") == []
