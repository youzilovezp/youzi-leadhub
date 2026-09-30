"""三层归一测试（报告 3.4）。"""
from app.normalize import entity_key, normalize_phone, normalize_url


def test_normalize_url():
    assert normalize_url("HTTPS://Example.COM/path/?utm_source=x&keep=1#frag") == \
        "https://example.com/path?keep=1"
    assert normalize_url("https://a.b/contact/") == "https://a.b/contact"
    assert normalize_url("ftp://x/") is None
    assert normalize_url("not a url") is None


def test_normalize_url_path_space_encoding():
    """P1 修复（2026-09-30）：路径空格百分号编码——"path with space" 与
    "path%20with%20space" 必须同源（dupefilter 同 fingerprint）。
    """
    a = normalize_url("https://a.com/path with space")
    b = normalize_url("https://a.com/path%20with%20space")
    assert a == b, f"空格与编码两种写法应同 fingerprint: {a} vs {b}"
    assert a is not None and "%20" in a  # 验证编码生效
    # 安全字符不应编码
    assert normalize_url("https://a.com/path-1_2.x") == "https://a.com/path-1_2.x"


def test_phone_link_layer_imply_plus():
    assert normalize_phone("8613800138000", imply_plus=True) == ("+8613800138000", "CN")
    assert normalize_phone("%2B14155552671", imply_plus=True) == ("+14155552671", "US")
    assert normalize_phone("+62 812-3456-7890", imply_plus=True)[1] == "ID"


def test_phone_text_layer_international():
    assert normalize_phone("+852 2123 4567") == ("+85221234567", "HK")
    assert normalize_phone("0086 138 0013 8000") == ("+8613800138000", "CN")


def test_phone_invalid():
    assert normalize_phone("08123456") is None            # 本地格式、缺国家码
    assert normalize_phone("+1234") is None                # 太短
    assert normalize_phone("+999999999999999999") is None  # 超 15 位
    assert normalize_phone("hello") is None


def test_phone_plus_zero_zero_prefix():
    """B2 修复（2026-09-30）：+00... 前缀应被接受（与 00... 同义）——libphonenumber 兜底 E164。
    旧实现只拦无 + 的 00...；+00... 走 libphonenumber 直接被拒 → None。
    """
    assert normalize_phone("+0086 138 0013 8000") == ("+8613800138000", "CN")
    assert normalize_phone("0086 138 0013 8000") == ("+8613800138000", "CN")
    assert normalize_phone("+00 86 138 0013 8000") == ("+8613800138000", "CN")


def test_entity_key():
    assert entity_key("www.example.com") == "example.com"
    assert entity_key("example.co.uk") == "example.co.uk"
    # PSL 私有段平台后缀：实体键回退完整 host（报告 3.4 平台后缀例外）
    assert entity_key("shop123.myshopify.com") == "shop123.myshopify.com"
    assert entity_key("blog.blogspot.com") == "blog.blogspot.com"


def test_entity_key_idn_normalization():
    """P1 修复（2026-09-30）：IDN 域名 native / punycode 两种写法归一为同一 key。
    避免同一实体在不同 seed 渠道写成两条 domain 行、sighting 永久分裂。
    """
    assert entity_key("例え.テスト") == entity_key("xn--r8jz45g.xn--zckzah")
    # 中国 IDN
    assert entity_key("中国.中国") == entity_key("xn--fiqs8s.xn--fiqs8s")


def test_entity_key_strips_port():
    """P1 修复：端口静默剥离——entity_key("example.com:8080") == "example.com"
    不同端口（开发/预发/prod 跨 CDN）共享 entity_key 与预算。
    """
    assert entity_key("example.com:8080") == "example.com"
    assert entity_key("example.com:9443") == "example.com"
    assert entity_key("www.example.com:8443") == "example.com"  # eTLD+1 去 www


def test_settings_dedupefilter_class_wired():
    """CRIT #3 修复：Scrapy dupefilter 必须能剥 utm_/fbclid/gclid（spec 3.4 URL 级
    归一要求；旧 REQUEST_FINGERPRINTER_IMPLEMENTATION='2.7' 是 Scrapy 2.19 dead code，
    实际无效——改用自定义 REQUEST_FINGERPRINTER_CLASS 走同构 normalize_url 链路）。"""
    from app.settings import REQUEST_FINGERPRINTER_CLASS
    assert REQUEST_FINGERPRINTER_CLASS.endswith("YouziUrlFingerprinter"), \
        f"REQUEST_FINGERPRINTER_CLASS 必须显式指向自定义 fingerprinter：{REQUEST_FINGERPRINTER_CLASS!r}"


def test_youzi_dupefilter_strips_utm():
    from app.dupefilter import YouziUrlFingerprinter
    from scrapy.http import Request

    fp = YouziUrlFingerprinter()
    seen_a = fp.fingerprint(Request("https://x.com/contact?utm_source=fb&keep=1"))
    seen_b = fp.fingerprint(Request("https://x.com/contact?keep=1&utm_source=ig"))
    assert seen_a == seen_b, "utm_* 必须从 fingerprint 中剥除（同 URL 不同 utm_source 应去重）"


def test_youzi_dupefilter_keeps_distinct():
    from app.dupefilter import YouziUrlFingerprinter
    from scrapy.http import Request

    fp = YouziUrlFingerprinter()
    a = fp.fingerprint(Request("https://x.com/contact"))
    b = fp.fingerprint(Request("https://x.com/about"))
    assert a != b, "路径不同 URL 应保留不同 fingerprint"


def test_normalize_url_rejects_control_chars():
    """MED-LOW fix：URL 含 ASCII 控制字符应被拒（浏览器/服务器会拒，爬到也是污染）。"""
    assert normalize_url("http://x.com/\x00bad") is None
    assert normalize_url("http://x.com/\x01\x02\x03") is None
    assert normalize_url("http://x.com/\x7fdel") is None
    # 正常字符应通过
    assert normalize_url("http://x.com/path") is not None


def test_normalize_url_empty_input():
    assert normalize_url("") is None
    assert normalize_url(None) is None   # type: ignore[arg-type]


def test_normalize_url_keeps_port():
    """P2 修复（2026-09-28）：非默认端口必须保留——:8443 种子曾被归一到 443 打错
    origin；默认端口（80/443）规范化去除，保持与裸 scheme 指纹同源。"""
    assert normalize_url("https://example.com:8443/x") == "https://example.com:8443/x"
    assert normalize_url("http://example.com:8080/") == "http://example.com:8080/"
    assert normalize_url("https://example.com:443/") == "https://example.com/"
    assert normalize_url("http://example.com:80/x") == "http://example.com/x"
    assert normalize_url("https://example.com:notaport/") is None  # 畸形端口拒收
