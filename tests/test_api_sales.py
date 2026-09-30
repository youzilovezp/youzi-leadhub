"""销售 UX API 端点测试（2026-09-30 新增 4 个端点：/api/today /api/progress /api/leads/{id}/status）。

端到端覆盖：everpro.id / facebook.com 等真实数据已通过这些端点被前端消费。
"""
import os

import pytest
from fastapi.testclient import TestClient

from app import db as dbm
from app import api as api_mod

FIX = dbm  # 别名


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """每个测试独立 DB + 重置 _conn lru_cache + 覆盖模块级 DB_PATH。

    DB_PATH = os.environ.get("BSP_DB", "data/leads.db") 在 app.api import 时已固化
    （模块级常量），patchenv 只影响后续 API 调用，不改变已加载的字符串值——
    必须同时 patch DB_PATH。
    """
    dbp = str(tmp_path / "t.db")
    monkeypatch.setenv("BSP_DB", dbp)
    monkeypatch.setattr(api_mod, "DB_PATH", dbp)
    api_mod._conn.cache_clear()
    conn = dbm.connect(dbp)
    yield conn, dbp
    conn.close()
    api_mod._conn.cache_clear()


def _seed(conn, entity, channel, market, market_group, score, p0,
          phone, contact_status="new"):
    dbm.upsert_domain(conn, entity, channel, None)
    dbm.upsert_phone(conn, phone, market)
    dbm.upsert_sighting(conn, entity, phone, f"https://{entity}/", "link")
    conn.execute(
        """UPDATE domain SET market=?, market_group=?, score=?, p0=?, contact_status=?
           WHERE entity_key=?""",
        (market, market_group, score, p0, contact_status, entity),
    )
    conn.commit()


# ============================================================================
# 1. /api/today：今日联系队列（核心端点）
# ============================================================================

def test_today_returns_p0_first_with_outreach_draft(fresh_db):
    """/api/today 必须按 P0 优先 + score DESC 排，每条含 outreach 草稿。"""
    conn, dbp = fresh_db
    # 3 个 P0 + 5 个非 P0（new）；1 个 contacted（应不出现）
    _seed(conn, "p0-a.cn", "play", "ID", "SEA", 20, 1, "+628123456789")
    _seed(conn, "p0-b.cn", "play", "ID", "SEA", 19, 1, "+628123456789")
    _seed(conn, "p0-c.cn", "play", "ID", "SEA", 18, 1, "+628123456789")
    for i in range(5):
        _seed(conn, f"normal-{i+1}", "osm", "BR", "LATAM", 10 - i, 0, "+5511999998888")
    _seed(conn, "contacted.com", "play", "CN", "CN", 99, 1, "+8613800138777",
          contact_status="contacted")
    conn.commit()

    client = TestClient(api_mod.app)
    r = client.get("/api/today")
    assert r.status_code == 200
    data = r.json()
    entities = [d["entity_key"] for d in data]
    # 3 个 P0 在前（按 score DESC：a=20, b=19, c=18）
    assert entities[:3] == ["p0-a.cn", "p0-b.cn", "p0-c.cn"], f"got {entities[:3]}"
    # 后续 5 个非 P0
    assert entities[3:8] == ["normal-1", "normal-2", "normal-3", "normal-4", "normal-5"]
    # contacted 不在今日队列（销售不能重复联系）
    assert "contacted.com" not in entities
    # SEA 草稿用印尼语（"memperhatikan" / "BSP"）
    assert "BSP" in data[0]["outreach"]["message"]
    assert data[0]["outreach"]["language"] == "id"
    # LATAM 草稿用葡语
    assert "vocês" in data[3]["outreach"]["message"].lower()
    assert data[3]["outreach"]["language"] == "pt"
    # wa.me deep link 正确生成
    assert data[0]["outreach"]["whatsapp_deep_link"] == "https://wa.me/628123456789"


# ============================================================================
# 2. /api/progress：本周进度（quota 进度条核心数据）
# ============================================================================

def test_progress_counts_week_contacts_and_status_breakdown(fresh_db):
    """/api/progress 报告本周联系数 + 状态分布——quota 进度条核心数据。"""
    conn, dbp = fresh_db
    # 本周内（2 天前 contacted + 5 天前 won）
    conn.execute("UPDATE domain SET contact_status='new' WHERE entity_key LIKE 'new-%'")
    dbm.upsert_domain(conn, "new-1", "play", None)
    dbm.upsert_domain(conn, "new-2", "play", None)
    dbm.upsert_domain(conn, "contacted-1", "play", None)
    conn.execute("UPDATE domain SET contact_status='contacted', contacted_at=datetime('now','-2 days') "
                 "WHERE entity_key='contacted-1'")
    dbm.upsert_domain(conn, "won-1", "play", None)
    conn.execute("UPDATE domain SET contact_status='won', contacted_at=datetime('now','-5 days') "
                 "WHERE entity_key='won-1'")
    dbm.upsert_domain(conn, "lost-1", "play", None)
    # lost 是 10 天前——不在本周
    conn.execute("UPDATE domain SET contact_status='lost', contacted_at=datetime('now','-10 days') "
                 "WHERE entity_key='lost-1'")
    conn.commit()

    client = TestClient(api_mod.app)
    r = client.get("/api/progress")
    assert r.status_code == 200
    data = r.json()
    # 本周内：contacted-1 (2天) + won-1 (5天) = 2 个；lost-1 是 10 天不在
    assert data["week_contacts"] == 2, f"got {data['week_contacts']}"
    # 4 种状态都在分布里
    assert set(data["by_status"].keys()) == {"new", "contacted", "won", "lost"}
    # total = 5
    assert data["total"] == 5
    # new_today = 2 个 new 实体
    assert data["new_today"] == 2


# ============================================================================
# 3. PATCH /api/leads/{id}/status：标记跟进（销售每天点几十次）
# ============================================================================

def test_patch_lead_status_updates_contacted_at(fresh_db):
    """PATCH 状态机：contacted/in_conversation/won/lost 自动戳 contacted_at；new 不戳。"""
    conn, dbp = fresh_db
    dbm.upsert_domain(conn, "lead-1", "play", None)
    conn.commit()

    client = TestClient(api_mod.app)
    # 标 contacted——contacted_at 应有值
    r = client.patch("/api/leads/lead-1/status",
                     json={"status": "contacted", "notes": "已报价"})
    assert r.status_code == 200, f"got {r.status_code}: {r.text}"
    assert r.json()["notes"] == "已报价"

    conn2 = dbm.connect(dbp)
    row = conn2.execute(
        "SELECT contact_status, contacted_at, contact_notes FROM domain WHERE entity_key='lead-1'").fetchone()
    assert row["contacted_at"] is not None, "contacted_at 应已戳"
    assert row["contact_status"] == "contacted"
    assert row["contact_notes"] == "已报价"
    first_contacted_at = row["contacted_at"]
    conn2.close()

    # 改回 new——contacted_at 保留（首次联系时间不变）
    client.patch("/api/leads/lead-1/status", json={"status": "new"})
    conn3 = dbm.connect(dbp)
    row3 = conn3.execute(
        "SELECT contact_status, contacted_at FROM domain WHERE entity_key='lead-1'").fetchone()
    assert row3["contacted_at"] == first_contacted_at, "new 不应覆盖 contacted_at"
    assert row3["contact_status"] == "new"
    conn3.close()


def test_patch_lead_status_invalid_input(fresh_db):
    """PATCH 错误路径：不存在实体 404 + 非法 status 400。"""
    conn, dbp = fresh_db
    dbm.upsert_domain(conn, "real.com", "play", None)
    conn.commit()

    client = TestClient(api_mod.app)
    # 不存在
    r404 = client.patch("/api/leads/ghost/status", json={"status": "contacted"})
    assert r404.status_code == 404
    # 非法 status
    r400 = client.patch("/api/leads/real.com/status", json={"status": "nonsense"})
    assert r400.status_code == 400


# ============================================================================
# 2026-10-01：付费富化端点（hunter / builtwith）
# ============================================================================

# ============================================================================
# 2026-10-01：灰域隔离（五代理审计 P0-4）——博彩/demo 域不进销售视野
# ============================================================================

def _seed_gambling(conn, entity, gambling=1):
    _seed(conn, entity, "play", "ID", "SEA", 8, 0, "+628999999999")
    conn.execute("UPDATE domain SET gambling=? WHERE entity_key=?", (gambling, entity))
    conn.commit()


def test_leads_excludes_gambling_by_default(fresh_db):
    """博彩域混在 /api/leads 尾部且 CSV 全量导出（实测 11 个 gambling=1 带号域）
    ——默认排除，?include_gray=true 显式放行。"""
    conn, dbp = fresh_db
    _seed(conn, "clean-shop.com", "play", "ID", "SEA", 10, 0, "+628111111111")
    _seed_gambling(conn, "casino-x.com")
    client = TestClient(api_mod.app)

    entities = [d["entity"] for d in client.get("/api/leads").json()]
    assert entities == ["clean-shop.com"], entities
    entities_gray = [d["entity"] for d in
                     client.get("/api/leads?include_gray=true").json()]
    assert set(entities_gray) == {"clean-shop.com", "casino-x.com"}


def test_today_excludes_gambling(fresh_db):
    """今日联系队列同样排灰域（销售不该把预算花在博彩域上）。"""
    conn, dbp = fresh_db
    _seed(conn, "clean-shop.com", "play", "ID", "SEA", 10, 0, "+628111111111")
    _seed_gambling(conn, "casino-x.com")
    client = TestClient(api_mod.app)
    entities = [d["entity_key"] for d in client.get("/api/today").json()]
    assert entities == ["clean-shop.com"], entities


def test_export_csv_excludes_gambling_by_default(fresh_db):
    """CSV 导出默认排灰域；include_gray=True 全量（审计对账用）。"""
    import csv as _csv
    from app import stats as stats_mod
    conn, dbp = fresh_db
    _seed(conn, "clean-shop.com", "play", "ID", "SEA", 10, 0, "+628111111111")
    _seed_gambling(conn, "casino-x.com")

    out1 = str(fresh_db[1]).replace("t.db", "export-default.csv")
    n = stats_mod.export_csv(conn, out1)
    rows = list(_csv.DictReader(open(out1, encoding="utf-8")))
    assert n == 1 and rows[0]["entity"] == "clean-shop.com"

    out2 = str(fresh_db[1]).replace("t.db", "export-gray.csv")
    n2 = stats_mod.export_csv(conn, out2, include_gray=True)
    assert n2 == 2


def test_enrich_endpoint_requires_known_provider(fresh_db):
    """未知 provider → 400（不能让用户随便填）。"""
    conn, dbp = fresh_db
    dbm.upsert_domain(conn, "x.com", "play", None)
    conn.commit()
    client = TestClient(api_mod.app)
    r = client.post("/api/crawler/enrich/x.com?provider=ghost")
    assert r.status_code == 400


def test_enrich_endpoint_404_for_missing_entity(fresh_db):
    """实体不存在 → 404（避免静默无操作）。"""
    client = TestClient(api_mod.app)
    r = client.post("/api/crawler/enrich/ghost.com?provider=hunter")
    assert r.status_code == 404


def test_enrich_endpoint_graceful_when_no_api_key(fresh_db, monkeypatch):
    """2026-10-01：没设 API KEY 时降级返回空 delta，**不报错**——
    让 CLI / 调用方可以无脑调用，等用户配 KEY 才生效。"""
    monkeypatch.delenv("YOUZI_HUNTER_API_KEY", raising=False)
    conn, dbp = fresh_db
    dbm.upsert_domain(conn, "x.com", "play", None)
    conn.commit()
    client = TestClient(api_mod.app)
    r = client.post("/api/crawler/enrich/x.com?provider=hunter")
    assert r.status_code == 200
    body = r.json()
    assert body["entity"] == "x.com"
    assert body["provider"] == "hunter"
    assert body["delta"] == {}
    assert body["updated"] is False
    # 域表 enrichment_status 保持 'none'（未富化）
    assert dbm.connect(dbp).execute(
        "SELECT enrichment_status FROM domain WHERE entity_key='x.com'"
    ).fetchone()[0] == "none"


def test_enrich_endpoint_updates_domain_when_provider_returns_data(fresh_db, monkeypatch):
    """2026-10-01：Provider 返回非空 delta 时落库（contact_email/tech_signals）。"""
    from app.enrich import HunterProvider
    monkeypatch.setattr(HunterProvider, "enrich",
                        lambda self, e, c: {"contact_email": "info@x.com",
                                            "tech_signals": "shopify,stripe"})
    conn, dbp = fresh_db
    dbm.upsert_domain(conn, "x.com", "play", None)
    conn.commit()
    client = TestClient(api_mod.app)
    r = client.post("/api/crawler/enrich/x.com?provider=hunter")
    assert r.status_code == 200
    assert r.json()["updated"] is True
    row = dbm.connect(dbp).execute(
        "SELECT contact_email, tech_signals, enrichment_status "
        "FROM domain WHERE entity_key='x.com'"
    ).fetchone()
    assert row[0] == "info@x.com"
    assert row[1] == "shopify,stripe"
    assert row[2] == "done"
