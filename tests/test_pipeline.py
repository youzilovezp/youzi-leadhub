"""管道幂等 + 打分落库测试（报告 9.1 冒烟项：同批跑两遍 sighting 一致）。"""
import sqlite3
from pathlib import Path

from youzi_bsp import db
from youzi_bsp.pipelines import WaStorePipeline
from youzi_bsp.score import page_flags
from youzi_bsp.stats import channel_stats

FIX = Path(__file__).parent / "fixtures"


def _items():
    return [
        {"url": "https://www.example-shop.cn/",
         "html": (FIX / "p0_zh.html").read_text(encoding="utf-8"),
         "channel": "sample", "seed_host": "www.example-shop.cn"},
        {"url": "https://www.example-shop.cn/contact",
         "html": (FIX / "wa_text.html").read_text(encoding="utf-8"),
         "channel": "sample", "seed_host": "www.example-shop.cn"},
    ]


def _run(tmp_path, times=1):
    for _ in range(times):
        pl = WaStorePipeline(db_path=str(tmp_path / "leads.db"))
        pl.open_spider(None)
        for it in _items():
            pl.process_item(it, None)
        pl.close_spider(None)
    return db.connect(tmp_path / "leads.db")


def test_counts_and_scoring(tmp_path):
    conn = _run(tmp_path)
    d = conn.execute("SELECT * FROM domain").fetchall()
    assert len(d) == 1
    row = d[0]
    assert row["status"] == "scored"
    assert row["p0"] == 1                       # ICP 备案 + 中文信号
    assert row["market"] == "CN"                # 两个号码国众数
    assert conn.execute("SELECT COUNT(*) FROM phone").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM sighting").fetchone()[0] == 2


def test_idempotency(tmp_path):
    _run(tmp_path, times=1)
    conn1 = db.connect(tmp_path / "leads.db")
    _run(tmp_path, times=1)  # 同批重跑
    conn2 = db.connect(tmp_path / "leads.db")
    for table in ("domain", "phone", "sighting"):
        assert conn1.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == \
            conn2.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], table


def test_page_flags():
    flags = page_flags((FIX / "p0_zh.html").read_text(encoding="utf-8"))
    assert flags["icp"] is True
    assert flags["lang"] == "zh"
    assert flags["title_zh"] is True


def test_forget_deletes_entity_and_orphan_phones(tmp_path):
    """GDPR 删除（报告 7.4）：实体及证据删除；仅清无引用孤儿号码，一号多挂不误删。"""
    from youzi_bsp import db as dbm

    conn = dbm.connect(tmp_path / "t.db")
    for ent in ("a.com", "b.com"):
        dbm.upsert_domain(conn, ent, "x", None)
    dbm.upsert_phone(conn, "+11111111111", "US")
    dbm.upsert_phone(conn, "+22222222222", "US")
    dbm.upsert_sighting(conn, "a.com", "+11111111111", "https://a.com/", "link")
    dbm.upsert_sighting(conn, "b.com", "+22222222222", "https://b.com/", "link")

    assert dbm.forget(conn, "a.com") == 1
    assert conn.execute("SELECT COUNT(*) FROM domain").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM sighting").fetchone()[0] == 1
    phones = [r[0] for r in conn.execute("SELECT e164 FROM phone")]
    assert "+11111111111" not in phones and "+22222222222" in phones
    assert dbm.forget(conn, "a.com") == 0  # 幂等


def test_detect_emails():
    from youzi_bsp.detect import detect

    res = detect('<a href="mailto:Sales@Example.com?subject=hi">m</a>'
                 '<a href="mailto:bad addr@x.com">bad</a>')
    assert res["emails"] == ["sales@example.com"]  # 小写归一、剥查询串、拒含空格


def test_p0_strong_signals_only():
    from youzi_bsp.score import score_domain

    # D1-3 实测教训：hreflang zh / 中文标题在跨国站误报（github/google/stripe）
    weak = {"lang": "en", "icp": False, "hreflang_zh": True, "title_zh": False}
    assert score_domain(weak, ["US"], "wikipedia.org")["p0"] == 0
    # 强信号：中国 TLD
    assert score_domain(weak, [], "vstarcam.cn")["p0"] == 1
    # 强信号：ICP 备案
    assert score_domain({"lang": "en", "icp": True, "hreflang_zh": False, "title_zh": False},
                        ["US"], "example.com")["p0"] == 1
    # 强信号：中文页面 + CN 市场双确认
    assert score_domain({"lang": "zh", "icp": False, "hreflang_zh": False, "title_zh": False},
                        ["CN"], "some-brand.com")["p0"] == 1


def test_gambling_downscored():
    from youzi_bsp.score import score_domain

    # 页面博彩词命中
    assert page_flags("<html lang='en'><title>shop</title>"
                      "online casino betting tips</html>")["gambling"] is True
    assert page_flags("<html lang='en'><title>shop</title>"
                      "best slots booking</html>")["gambling"] is False
    # 页面/域名博彩：降分 + 掉出 P0（.cn 博彩域也不能进优先层）
    g = score_domain({"lang": "en", "icp": False, "hreflang_zh": False,
                      "title_zh": False, "gambling": True}, ["US"], "x.com")
    assert g["p0"] == 0 and g["score"] == 0            # 3 - 8 → floor 0
    g2 = score_domain({"lang": "en", "icp": False, "hreflang_zh": False,
                       "title_zh": False, "gambling": False}, ["US"], "sattamatkano1.me")
    assert g2["p0"] == 0 and g2["score"] == 0          # 域名子串命中同规则
    # 正常域不受影响
    ok = score_domain({"lang": "en", "icp": False, "hreflang_zh": False,
                       "title_zh": False, "gambling": False}, ["US"], "jotform.com")
    assert ok["score"] == 3


def test_none_page_writes_domain_without_sighting(tmp_path):
    pl = WaStorePipeline(db_path=str(tmp_path / "leads.db"))
    pl.open_spider(None)
    pl.process_item({"url": "https://consult.example/", "html": "<html lang='en'><body>x</body></html>",
                     "channel": "sample", "seed_host": "consult.example"}, None)
    pl.close_spider(None)
    conn = sqlite3.connect(str(tmp_path / "leads.db"))
    assert conn.execute("SELECT COUNT(*) FROM domain").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM sighting").fetchone()[0] == 0


def test_resume_scores_pending_domains(tmp_path):
    """D2 审计 #3：中断（close_spider 未跑）后续跑，新进程须能把遗留 pending 打分。"""
    dbp = str(tmp_path / "leads.db")
    pl = WaStorePipeline(db_path=dbp)
    pl.open_spider(None)
    pl.process_item(_items()[0], None)      # flags 已逐 item 落库
    pl.conn.commit()
    pl.conn.close()                          # 模拟进程死在中途
    pl2 = WaStorePipeline(db_path=dbp)       # "续跑"：新进程无旧内存态
    pl2.open_spider(None)
    pl2.close_spider(None)
    conn = db.connect(dbp)
    row = conn.execute("SELECT status, p0, market FROM domain").fetchone()
    assert row["status"] == "scored"
    assert row["p0"] == 1                    # ICP 信号来自 DB 持久化列，非内存
    assert row["market"] == "CN"


def _error_item():
    """spider errback 产出的失败种子 item（无 html，带 error 标记）。"""
    return {"url": "https://down.example.org/", "channel": "sample",
            "seed_host": "down.example.org", "error": True}


def test_error_item_marks_domain_error(tmp_path):
    """失败种子留痕：error item 落库 status='error'，close_spider 不覆盖成 scored。"""
    pl = WaStorePipeline(db_path=str(tmp_path / "leads.db"))
    pl.open_spider(None)
    pl.process_item(_error_item(), None)
    pl.close_spider(None)
    rows = db.connect(tmp_path / "leads.db").execute(
        "SELECT status FROM domain").fetchall()
    assert len(rows) == 1 and rows[0]["status"] == "error"


def test_channel_stats_denominator_includes_error(tmp_path):
    """hit_rate 分母诚实化：1 scored + 1 error → total==2、hits==1。"""
    _run(tmp_path)                            # 1 个 scored 域（有 sighting）
    pl = WaStorePipeline(db_path=str(tmp_path / "leads.db"))
    pl.open_spider(None)
    pl.process_item(_error_item(), None)
    pl.close_spider(None)
    stats = channel_stats(db.connect(tmp_path / "leads.db"))
    assert len(stats) == 1
    assert stats[0]["total"] == 2
    assert stats[0]["hits"] == 1
    assert stats[0]["hit_rate"] == 0.5


def test_success_revives_error_domain(tmp_path):
    """同一域上次失败留 error、这次重爬成功：复活为 pending 并正常打分。"""
    dbp = str(tmp_path / "leads.db")
    pl = WaStorePipeline(db_path=dbp)
    pl.open_spider(None)
    pl.process_item({"url": "https://www.example-shop.cn/", "channel": "sample",
                     "seed_host": "www.example-shop.cn", "error": True}, None)
    pl.close_spider(None)
    _run(tmp_path)                            # 重爬成功
    row = db.connect(dbp).execute("SELECT status, p0 FROM domain").fetchone()
    assert row["status"] == "scored"
    assert row["p0"] == 1


def test_error_item_never_degrades_crawled_or_scored(tmp_path):
    """errback 也挂子页请求：部分成功（首页 OK + 同 host 子页失败）不得降级 error
    （否则 score 清零 + sighting 被导出成 score=0 脏行）；已 scored 域重爬失败同理。"""
    dbp = str(tmp_path / "leads.db")
    pl = WaStorePipeline(db_path=dbp)
    pl.open_spider(None)
    pl.process_item(_items()[0], None)        # 首页成功：last_crawled 已写
    pl.process_item({"url": "https://www.example-shop.cn/contact", "channel": "sample",
                     "seed_host": "www.example-shop.cn", "error": True}, None)
    pl.close_spider(None)
    row = db.connect(dbp).execute("SELECT status, p0, score FROM domain").fetchone()
    assert row["status"] == "scored" and row["p0"] == 1
    # 已 scored 域（last_crawled 也非 NULL）再遇 error item：status/score 均不变
    pl2 = WaStorePipeline(db_path=dbp)
    pl2.open_spider(None)
    pl2.process_item({"url": "https://www.example-shop.cn/", "channel": "sample",
                      "seed_host": "www.example-shop.cn", "error": True}, None)
    pl2.close_spider(None)
    row2 = db.connect(dbp).execute("SELECT status, p0, score FROM domain").fetchone()
    assert row2["status"] == "scored"
    assert row2["score"] == row["score"]
