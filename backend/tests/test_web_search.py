"""web_search 采集器测试（PRD §6.2 P1 搜索数据源）。"""

import pytest


def test_results_to_drafts_filters_platforms():
    """搜索结果 → 种子：滤平台/社媒域、同域去重、标题取分隔符前主体。"""
    from app.collectors.web_search import results_to_drafts

    items = [
        {"title": "Acme Trading - Premium Supplier", "url": "https://www.acme-trading.com/about"},
        {"title": "Acme Trading 另一页", "url": "https://acme-trading.com/products"},  # 同域去重
        {"title": "Facebook", "url": "https://www.facebook.com/acme"},  # 平台域滤掉
        {"title": "Wholesale Central | Suppliers", "url": "https://alibaba.com/supplier"},  # 平台域
        {"title": "Shenzhen Glow Tech Co Ltd", "url": "https://glowtech.com.sg/"},  # 海外 ccTLD 官网
        {"title": "", "url": "https://empty-title.com/"},  # 无标题丢弃
    ]
    drafts = results_to_drafts(items)
    assert len(drafts) == 2
    assert drafts[0].name == "Acme Trading"  # " - Premium Supplier" 已剥
    assert drafts[0].website == "https://acme-trading.com"  # 归一为站点根
    assert drafts[0].source == "web_search"
    assert drafts[1].name == "Shenzhen Glow Tech Co Ltd"
    assert drafts[1].website == "https://glowtech.com.sg"


def test_bing_shape_items_supported():
    """Bing webPages.value[] 形态（name/url 键）同样可解析。"""
    from app.collectors.web_search import results_to_drafts

    items = [{"name": "Bloom Cosmetics", "url": "https://bloomcosmetics.my/shop"}]
    drafts = results_to_drafts(items)
    assert len(drafts) == 1 and drafts[0].name == "Bloom Cosmetics"


def test_parse_ddg_html_unwraps_redirects():
    """DDG HTML 结果解析：跳转链接解出真实 URL、标题剥内标签。"""
    from app.collectors.web_search import parse_ddg_html

    html = """
    <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Facme-trading.com%2Fabout&amp;rut=abc">
      Acme <b>Trading</b> Co</a>
    <a class="result__a" href="https://direct.com/page">Direct Site</a>
    """
    items = parse_ddg_html(html)
    assert items == [
        {"title": "Acme Trading Co", "url": "https://acme-trading.com/about"},
        {"title": "Direct Site", "url": "https://direct.com/page"},
    ]


def test_validate_default_engine_needs_no_credentials(monkeypatch):
    """默认引擎 duckduckgo 零凭据可用；选付费引擎缺凭据时报可行动错误。"""
    from app.collectors.web_search import WebSearchCollector

    monkeypatch.setattr("app.core.config.settings.SEARCH_ENGINE", "duckduckgo")
    WebSearchCollector().validate_params({"keywords": "whatsapp supplier"})  # 不抛

    from app.core.exceptions import BusinessError

    monkeypatch.setattr("app.core.config.settings.SEARCH_ENGINE", "google_cse")
    monkeypatch.setattr("app.core.config.settings.GOOGLE_CSE_KEY", "")
    with pytest.raises(Exception) as ei:
        WebSearchCollector().validate_params({"keywords": "x"})
    assert isinstance(ei.value, BusinessError) and "GOOGLE_CSE_KEY" in ei.value.message


def test_detect_domain_tld_overseas():
    """海外域名信号（§4.2）：ccTLD 命中；.com/.cn 不算。"""
    from app.collectors.overseas import detect_domain_tld

    assert detect_domain_tld("https://glowtech.com.sg/") == "sg"
    assert detect_domain_tld("https://shop.example.my/products") == "my"
    assert detect_domain_tld("https://www.acme.com/") is None  # gTLD
    assert detect_domain_tld("https://cncompany.cn/") is None  # 中国域名
    assert detect_domain_tld(None) is None


def test_article_pages_filtered():
    """搜索词「whatsapp 客服」类的内容页结果（指南/测评/博客路径）不是企业种子。"""
    from app.collectors.web_search import results_to_drafts

    items = [
        {"title": "跨境电商WhatsApp客服必备功能指南", "url": "https://www.zoho.com.cn/desk/articles/whatsapp-ticketing"},  # 标题+路径双中
        {"title": "2026跨境电商必看：5款WhatsApp工具测评", "url": "https://www.163.com/dy/article/ABC.html"},  # 标题中
        {"title": "WhatsApp运营指南", "url": "https://blog.respon.ai/zh/docs/whatsapp-guide"},  # 路径中
        {"title": "Glow Tech Official Site", "url": "https://glowtech.com.sg/products/led-light"},  # 企业官网 ✓
    ]
    drafts = results_to_drafts(items)
    assert len(drafts) == 1
    assert drafts[0].name == "Glow Tech Official Site"
    # 种子入口归一为站点根（富化从首页开始）
    assert drafts[0].website == "https://glowtech.com.sg"
