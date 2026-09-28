"""OSM 直标签导入纯变换测试（无网络）。"""
from youzi_bsp.osm_direct import elements_to_rows


def test_elements_to_rows_variants():
    els = [
        # wa.me 链接形态 + website（contact:whatsapp 语义最强）
        {"type": "node", "id": 1, "tags": {
            "website": "https://kopisusu.co.id",
            "contact:whatsapp": "https://wa.me/628123456789"}},
        # 裸号码 + phone 标签（分隔符混写）
        {"type": "way", "id": 2, "tags": {
            "website": "http://warung-budi.my",
            "phone": "+60 3-7805 4479"}},
        # 无 website → 跳过（实体键无落点）
        {"type": "node", "id": 3, "tags": {"phone": "+6621234567"}},
        # 无效号码 → 跳过
        {"type": "node", "id": 4, "tags": {
            "website": "https://x.com", "phone": "12345"}},
    ]
    rows = elements_to_rows(els)
    assert len(rows) == 2
    assert rows[0]["e164"] == "+628123456789" and rows[0]["country"] == "ID"
    assert rows[0]["osm_url"].endswith("/node/1")           # 合规留痕：OSM 元素链接
    assert rows[1]["e164"] == "+60378054479" and rows[1]["country"] == "MY"
    assert rows[1]["osm_url"].endswith("/way/2")
