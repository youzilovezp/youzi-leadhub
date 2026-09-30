"""OSM 直标签导入纯变换测试（无网络）。"""
from pathlib import Path

from app.osm_direct import elements_to_rows


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


def test_import_osm_preserves_crawled_signals(tmp_path, monkeypatch):
    """P1 修复（2026-09-28）：import-osm 不得用空 flags 覆盖已爬取的 p0/分数
    （ICP/developer_name 等持久化信号保留），且补齐 market_group——导入打分改走
    close_spider 同款 DB 派生路径（score_pending）。"""
    import app.osm_direct as od
    from app import db as dbm
    from app.pipelines import WaStorePipeline

    FIX = Path(__file__).parent / "fixtures"
    dbp = str(tmp_path / "leads.db")
    # 先爬取：ICP 备案中文页 → p0=1、score=13（10 p0 + 3 有号码）
    pl = WaStorePipeline(db_path=dbp)
    pl.open_spider(None)
    pl.process_item({"url": "https://www.example-shop.cn/",
                     "html": (FIX / "p0_zh.html").read_text(encoding="utf-8"),
                     "channel": "sample", "seed_host": "www.example-shop.cn"}, None)
    pl.close_spider(None)
    before = dbm.connect(dbp).execute(
        "SELECT p0, score, market FROM domain"
        " WHERE entity_key='example-shop.cn'").fetchone()
    assert before["p0"] == 1

    # 再导入同实体 OSM 直标签（mock Overpass 响应，无网络）
    monkeypatch.setattr(od, "overpass_post", lambda q: {"elements": [
        {"type": "node", "id": 9, "tags": {
            "website": "https://www.example-shop.cn",
            "contact:whatsapp": "+8613800138000"}}]})
    assert od.import_osm("CN", 10, dbp) == 1
    row = dbm.connect(dbp).execute(
        "SELECT p0, score, market, market_group FROM domain"
        " WHERE entity_key='example-shop.cn'").fetchone()
    assert row["p0"] == 1                        # 不被 {"lang": None} 空 flags 覆盖
    assert row["score"] == before["score"]
    assert row["market"] == "CN"
    assert row["market_group"] == "CN"           # 导入路径补齐 market_group
