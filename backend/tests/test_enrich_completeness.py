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
    '<html><body><h1>Contact Us</h1>'
    '<p>Email: sales@acme.com or visit our FAQ.</p></body></html>'
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
    assert detect_text_phones([html]) == ["+86 137 3602 8159"]
    # 无国际前缀的座机形态不产出（误报面大）
    assert detect_text_phones(["<p>0755-12345678</p>"]) == []
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
