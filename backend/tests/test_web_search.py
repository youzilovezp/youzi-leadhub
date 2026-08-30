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
    assert drafts[0].website.startswith("https://www.acme-trading.com")
    assert drafts[0].source == "web_search"
    assert drafts[1].name == "Shenzhen Glow Tech Co Ltd"
    assert drafts[1].website == "https://glowtech.com.sg/"


def test_bing_shape_items_supported():
    """Bing webPages.value[] 形态（name/url 键）同样可解析。"""
    from app.collectors.web_search import results_to_drafts

    items = [{"name": "Bloom Cosmetics", "url": "https://bloomcosmetics.my/shop"}]
    drafts = results_to_drafts(items)
    assert len(drafts) == 1 and drafts[0].name == "Bloom Cosmetics"


def test_validate_requires_credentials(monkeypatch):
    """无搜索凭据 → 创建任务时即报可行动的业务错误。"""
    from app.collectors.web_search import WebSearchCollector

    monkeypatch.setattr("app.core.config.settings.GOOGLE_CSE_KEY", "")
    monkeypatch.setattr("app.core.config.settings.GOOGLE_CSE_CX", "")
    monkeypatch.setattr("app.core.config.settings.BING_SEARCH_KEY", "")

    from app.core.exceptions import BusinessError

    with pytest.raises(Exception) as ei:
        WebSearchCollector().validate_params({"keywords": "whatsapp supplier"})
    assert isinstance(ei.value, BusinessError)
    assert "GOOGLE_CSE_KEY" in str(ei.value.message)


def test_detect_domain_tld_overseas():
    """海外域名信号（§4.2）：ccTLD 命中；.com/.cn 不算。"""
    from app.collectors.overseas import detect_domain_tld

    assert detect_domain_tld("https://glowtech.com.sg/") == "sg"
    assert detect_domain_tld("https://shop.example.my/products") == "my"
    assert detect_domain_tld("https://www.acme.com/") is None  # gTLD
    assert detect_domain_tld("https://cncompany.cn/") is None  # 中国域名
    assert detect_domain_tld(None) is None
