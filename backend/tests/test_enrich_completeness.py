"""富化完整性修复测试（2026-08-31 审计批次1）。

- 邮箱检测扫全部页面（首页+联系/关于页），mailto 跨页优先
- 内页发现：中文锚文本（联系我们/产品）与英文 href 一并覆盖 + 同域过滤
- 证据链词表补 domain_tld
"""

from app.collectors.website_enrich import detect_email, find_inner_page_urls

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
