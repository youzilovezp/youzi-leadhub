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


def test_is_cn_requires_cjk_title():
    """is_cn 语义收紧（2026-08-31 巡检修 ICP 门完整性）：参数开 ∧ 标题含中文。

    英文标题的种子（英文/拼错关键词的自然结果）不吃盲标——否则 meta.com、
    gizmodo.com 这类纯海外站会以 is_cn=True 入库，富化出海外信号后以
    qualified 混进中国出海销售池。英文标题留给富化（ICP 备案/中文内容）判定。
    """
    from app.collectors.web_search import results_to_drafts

    items = [
        {"title": "深圳 glow 科技 官方网站", "url": "https://glow-cn-test.com/"},
        {"title": "WhatsApp from Meta", "url": "https://meta-cn-test.com/"},
        {"title": "Gizmodo Download Page", "url": "https://gizmodo-cn-test.com/"},
    ]
    drafts = results_to_drafts(items, params_is_cn="true")
    assert [d.is_cn for d in drafts] == [True, False, False]
    # 参数关：全部不标
    drafts_off = results_to_drafts(items, params_is_cn="false")
    assert not any(d.is_cn for d in drafts_off)


def test_drafts_with_stats_filter_breakdown():
    """滤因统计：平台域/内容页/同域重复分别计数（任务日志透明化）。"""
    from app.collectors.web_search import drafts_with_stats

    items = [
        {"title": "Normal Corp 官网", "url": "https://stat-normal.com/"},
        {"title": "Facebook Page", "url": "https://facebook.com/x"},  # 平台域
        {"title": "How to Use WhatsApp 指南", "url": "https://stat-article.com/guide"},  # 内容页
        {"title": "Same Corp Second Page", "url": "https://www.stat-normal.com/other"},  # 同域
    ]
    drafts, stats = drafts_with_stats(items)
    assert len(drafts) == 1
    assert stats == {"platform_domain": 1, "article_page": 1, "dup_domain": 1}


def test_parse_bing_html():
    """必应中国版结果页解析（b_algo 块的 h2 直链，无跳转包装）。"""
    from app.collectors.web_search import parse_bing_html

    html = """
    <li class="b_algo"><h2><a href="https://www.salemartly.com/" h="ID=SERP">SaleSmartly-<strong>WhatsApp</strong>私域运营</a></h2><p>desc</p></li>
    <li class="b_algo"><h2><a href="https://kuajingwang.vip/products">跨境王官网</a></h2></li>
    <li class="b_other">干扰块</li>
    """
    items = parse_bing_html(html)
    assert len(items) == 2
    assert items[0]["url"] == "https://www.salemartly.com/"
    assert "SaleSmartly" in items[0]["title"] and "WhatsApp" in items[0]["title"]
    assert parse_bing_html("") == []
    assert parse_bing_html("<html>no results</html>") == []


async def test_search_with_fallback_switches_engine(monkeypatch):
    """引擎降级链：主引擎（DDG）失败 → 自动切必应；主引擎正常时不切。"""
    from app.collectors import web_search as ws

    async def fake_ddg_fail(clients, engine, kw, limit):
        return [], "DDG 连接失败 ConnectError"

    async def fake_bing_ok(clients, kw, limit):
        return [{"title": "某公司官网", "url": "https://fallback-cn.com/"}], None

    monkeypatch.setattr(ws, "_search", fake_ddg_fail)
    monkeypatch.setattr(ws, "_search_bing", fake_bing_ok)
    logs = []

    async def log(level, msg):
        logs.append(msg)

    items, err, used = await ws.search_with_fallback((None, None), "whatsapp 客服", 10, log=log)
    assert used == "bing_cn" and err is None
    assert items[0]["url"] == "https://fallback-cn.com/"
    assert any("自动切换必应" in m for m in logs)

    # 主引擎正常 → 不动用降级
    async def fake_ok(clients, engine, kw, limit):
        return [{"title": "直接命中", "url": "https://primary-cn.com/"}], None

    monkeypatch.setattr(ws, "_search", fake_ok)
    items2, err2, used2 = await ws.search_with_fallback((None, None), "whatsapp 客服", 10)
    assert used2 == ws.settings.SEARCH_ENGINE and items2[0]["url"].startswith("https://primary")
