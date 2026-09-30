"""OSM 直标签导入纯变换测试（无网络）。"""
from pathlib import Path

from app.osm_direct import elements_to_rows


def test_elements_to_rows_variants():
    """2026-10-01 收窄：仅 contact:whatsapp 是 WA 证据——generic phone 标签
    （shop+phone/restaurant+phone）导入的商家电话未验证是 WA，冒充带号线索
    占了带号线索 47%（五代理审计 P0-3 最大假线索源）。"""
    els = [
        # wa.me 链接形态 + website（contact:whatsapp 语义最强）
        {"type": "node", "id": 1, "tags": {
            "website": "https://kopisusu.co.id",
            "contact:whatsapp": "https://wa.me/628123456789"}},
        # 裸号码 + phone 标签——2026-10-01 起不再当 WA 线索导入
        {"type": "way", "id": 2, "tags": {
            "website": "http://warung-budi.my",
            "phone": "+60 3-7805 4479"}},
        # contact:mobile 同样不认（未验证是 WA，只是手机号）
        {"type": "node", "id": 5, "tags": {
            "website": "http://mobile-only.example.my",
            "contact:mobile": "+60123456789"}},
        # 无 website → 跳过（实体键无落点）
        {"type": "node", "id": 3, "tags": {"phone": "+6621234567"}},
        # 无效号码 → 跳过
        {"type": "node", "id": 4, "tags": {
            "website": "https://x.com", "phone": "12345"}},
    ]
    rows = elements_to_rows(els)
    assert len(rows) == 1, f"仅 contact:whatsapp 应导入: {[r['entity'] for r in rows]}"
    assert rows[0]["e164"] == "+628123456789" and rows[0]["country"] == "ID"
    assert rows[0]["osm_url"].endswith("/node/1")           # 合规留痕：OSM 元素链接


def test_import_osm_query_only_whatsapp_tag(tmp_path, monkeypatch):
    """2026-10-01：Overpass 查询同步收窄——shop+phone / amenity+phone 分支
    返回的元素已不会被导入，查询它们纯属浪费公共端点配额。"""
    import app.osm_direct as od
    captured = {}

    def fake_post(q):
        captured["q"] = q
        return {"elements": []}

    monkeypatch.setattr(od, "overpass_post", fake_post)
    od.import_osm("MY", 10, str(tmp_path / "t.db"))
    q = captured["q"]
    assert "contact:whatsapp" in q
    assert '"shop"]["phone"]' not in q and '"phone"]["website"]' not in q, q


def test_elements_to_rows_skips_platform_root_hosts():
    """2026-10-01 巡检修复：website 落在平台根（google.com / facebook.com 等 PSL 不认
    为私有后缀但实为多商家平台）时跳过，否则 N 个不相关 OSM 商家会被 entity_key 塌成一
    条。myshopify.com 等已受 PSL 保护、不在本测试范围。"""
    els = [
        # 真实业务域名 → 保留
        {"type": "node", "id": 1, "tags": {
            "website": "https://kopisusu.co.id",
            "contact:whatsapp": "+628123456789"}},
        # sites.google.com 塌到 google.com → 跳过
        {"type": "node", "id": 2, "tags": {
            "website": "https://sites.google.com/view/kopi",
            "contact:whatsapp": "+628987654321"}},
        # facebook.com 主页（FB 商家当 website）→ 跳过
        {"type": "node", "id": 3, "tags": {
            "website": "https://www.facebook.com/warung.budi",
            "phone": "+60378054479"}},
        # fb.me 短链 → 跳过
        {"type": "node", "id": 4, "tags": {
            "website": "http://fb.me/warung",
            "phone": "+6621234567"}},
        # instagram.com 主页 → 跳过
        {"type": "node", "id": 5, "tags": {
            "website": "https://instagram.com/warung",
            "phone": "+62811234567"}},
    ]
    rows = elements_to_rows(els)
    assert len(rows) == 1, f"应只保留 1 条真实业务，过滤了 {len(els) - len(rows) - 1} 条平台塌域"
    assert rows[0]["e164"] == "+628123456789"


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
