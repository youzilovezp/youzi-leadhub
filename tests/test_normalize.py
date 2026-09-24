"""三层归一测试（报告 3.4）。"""
from youzi_bsp.normalize import entity_key, normalize_phone, normalize_url


def test_normalize_url():
    assert normalize_url("HTTPS://Example.COM/path/?utm_source=x&keep=1#frag") == \
        "https://example.com/path?keep=1"
    assert normalize_url("https://a.b/contact/") == "https://a.b/contact"
    assert normalize_url("ftp://x/") is None
    assert normalize_url("not a url") is None


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


def test_entity_key():
    assert entity_key("www.example.com") == "example.com"
    assert entity_key("example.co.uk") == "example.co.uk"
    # PSL 私有段平台后缀：实体键回退完整 host（报告 3.4 平台后缀例外）
    assert entity_key("shop123.myshopify.com") == "shop123.myshopify.com"
    assert entity_key("blog.blogspot.com") == "blog.blogspot.com"
