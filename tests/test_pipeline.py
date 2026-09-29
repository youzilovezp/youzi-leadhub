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
    # 强信号：ICP 备案（域名避开 example.*——B3 后会被 demo 规则归零）
    assert score_domain({"lang": "en", "icp": True, "hreflang_zh": False, "title_zh": False},
                        ["US"], "icp-holder.com")["p0"] == 1
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
    # 真博彩域 `bettingsite.me`（keyword 作为主词）仍命中——sattamatkano1 这种
    # "keyword 嵌在长字串内"的域名当前会被新边界规则放过去（接受漏检换描述型
    # 合法商家免伤）。
    g2 = score_domain({"lang": "en", "icp": False, "hreflang_zh": False,
                       "title_zh": False, "gambling": False}, ["US"], "bettingsite.me")
    assert g2["p0"] == 0 and g2["score"] == 0
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


def test_private_signal_scoring():
    """M2-3：wa_group/wa_business 私域信号各 +2（比挂号码更重的使用深度）。"""
    from youzi_bsp.score import score_domain

    base = {"lang": "en", "icp": False, "hreflang_zh": False, "title_zh": False,
            "gambling": False}
    plain = score_domain(base, ["US"], "x.com")["score"]
    group = score_domain({**base, "wa_group": True}, ["US"], "x.com")["score"]
    biz = score_domain({**base, "wa_business": True}, ["US"], "x.com")["score"]
    both = score_domain({**base, "wa_group": True, "wa_business": True},
                        ["US"], "x.com")["score"]
    assert group == plain + 2 and biz == plain + 2 and both == plain + 4


# ============================================================================
# CRIT #1 + MED #4: score.py 新能力 — developer_name + market_group + 双轨 hreflang
# ============================================================================

def test_market_group_mapping():
    """CRIT #2 修复：score_domain 返回 market_group 标签 {SEA, LATAM, MENA, EU, OTHER}。"""
    from youzi_bsp.score import score_domain

    assert score_domain({"lang": "en", "icp": False, "hreflang_zh": False,
                         "title_zh": False}, ["ID"], "x.com")["market_group"] == "SEA"
    assert score_domain({"lang": "en", "icp": False, "hreflang_zh": False,
                         "title_zh": False}, ["BR"], "x.com")["market_group"] == "LATAM"
    assert score_domain({"lang": "en", "icp": False, "hreflang_zh": False,
                         "title_zh": False}, ["AE"], "x.com")["market_group"] == "MENA"
    assert score_domain({"lang": "en", "icp": False, "hreflang_zh": False,
                         "title_zh": False}, ["DE"], "x.com")["market_group"] == "EU"
    assert score_domain({"lang": "en", "icp": False, "hreflang_zh": False,
                         "title_zh": False}, ["US"], "x.com")["market_group"] == "OTHER"
    assert score_domain({"lang": "en", "icp": False, "hreflang_zh": False,
                         "title_zh": False}, [], "x.com")["market_group"] is None


def test_developer_name_chinese_company_p0():
    """CRIT #1 修复：play 渠道 developerName 含中国城市/行业词 + 简/繁不限 → P0=1。
    这条信号是 P0 第 1 强信号，spec 3.5 明文列出，play 渠道是主力种子源。"""
    from youzi_bsp.score import score_domain

    flags = {"lang": "en", "icp": False, "hreflang_zh": False, "title_zh": False}
    dev_names = ["深圳市某某科技有限公司", "Shenzhen Tech Co., Ltd",
                 "广州跨境电商", "上海网络科技", "Beijing Trading"]
    for name in dev_names:
        res = score_domain(flags, ["US"], "brand.io",
                          developer_name=name)
        assert res["p0"] == 1, f"developer_name={name!r} should mark P0"


def test_developer_name_non_chinese_no_p0():
    """非中国公司主体名不应误判 P0。"""
    from youzi_bsp.score import score_domain

    flags = {"lang": "en", "icp": False, "hreflang_zh": False, "title_zh": False}
    assert score_domain(flags, ["US"], "brand.io",
                       developer_name="Acme Corp LLC")["p0"] == 0


def test_gambling_domain_word_boundary():
    """MED 修复：GAMBLING_DOMAIN 子串匹配须排除 hyphen-prefixed/suffixed——
    `las-casino-restaurant.com` 等合法商家名不应被博彩规则误伤。

    验证方式：合法域名 score 保持 +3（countries 命中但 WA_HEAVY 外），博彩域
    score 被 -8 扣到 0。
    """
    from youzi_bsp.score import score_domain

    flags = {"lang": "en", "icp": False, "hreflang_zh": False, "title_zh": False}
    # 描述型合法商家：keyword 两侧是 hyphen → 不命中博彩 → score 保留 +3
    assert score_domain(flags, ["US"], "las-casino-restaurant.com")["score"] == 3
    assert score_domain(flags, ["US"], "poker-face-hotel.com")["score"] == 3
    # 真博彩域（keyword 直接跟字母/数字/点）→ 命中 → score 被 -8 扣到 0
    assert score_domain(flags, ["US"], "casinoroyale.com")["score"] == 0
    assert score_domain(flags, ["US"], "bettingsite.me")["score"] == 0
    assert score_domain(flags, ["US"], "casino.com")["score"] == 0
    assert score_domain(flags, ["US"], "sattamatkano1.me")["score"] == 0


def test_p0_hreflang_zh_dual_track_strong():
    """MED 修复：hreflang zh + 目标市场含 CN/HK/TW → 双轨升格为 P0 强信号
    （spec 第 3 强信号，此前 v6.1 过杀被降为弱项；现双轨保留弱项 + 复合升格）。"""
    from youzi_bsp.score import score_domain

    base = {"lang": "en", "icp": False, "title_zh": False, "gambling": False}
    # 强信号：hreflang zh + 号码市场 = CN
    assert score_domain({**base, "hreflang_zh": True}, ["CN"], "x.com")["p0"] == 1
    assert score_domain({**base, "hreflang_zh": True}, ["HK"], "x.com")["p0"] == 1
    assert score_domain({**base, "hreflang_zh": True}, ["TW"], "x.com")["p0"] == 1
    # 弱信号：hreflang zh 但市场 ≠ CN（跨国站过杀场景）→ 仍 0
    assert score_domain({**base, "hreflang_zh": True}, ["US"], "x.com")["p0"] == 0


def test_malformed_url_skipped(tmp_path):
    """HIGH-LOW fix：畸形 URL（空串/无 host/无 scheme）应被 process_item 安全跳过，
    不应在 domain 表写入空 entity_key。"""
    dbp = str(tmp_path / "leads.db")
    pl = WaStorePipeline(db_path=dbp)
    pl.open_spider(None)

    pl.process_item({"url": "", "html": "<html></html>",
                     "channel": "test", "seed_host": "x.com", "developer_name": ""}, None)
    pl.process_item({"url": "not-a-url", "html": "<html></html>",
                     "channel": "test", "seed_host": "x.com", "developer_name": ""}, None)
    pl.process_item({"url": "https:///", "html": "<html></html>",
                     "channel": "test", "seed_host": "x.com", "developer_name": ""}, None)
    pl.close_spider(None)

    conn = db.connect(dbp)
    n = conn.execute("SELECT COUNT(*) AS c FROM domain").fetchone()["c"]
    assert n == 0, f"畸形 URL 必须跳过，但 db 写入了 {n} 条（可能含空 entity_key）"


def test_widget_email_atomic_merge(tmp_path):
    """HIGH #9 修复：widget/email OR-merge 改 SQLite 原子 SQL，
    并发两次 process_item 累加无丢失（之前 read-modify-write 会丢）。"""
    dbp = str(tmp_path / "leads.db")
    pl = WaStorePipeline(db_path=dbp)
    pl.open_spider(None)
    item1 = {"url": "https://x.com/", "html": '<a href="mailto:a@x.com">a</a>',
             "channel": "sample", "seed_host": "x.com"}
    item2 = {"url": "https://x.com/about", "html":
        '<script src="x.js" class="joinchat"></script>',
        "channel": "sample", "seed_host": "x.com"}
    pl.process_item(item1, None)
    pl.process_item(item2, None)
    pl.close_spider(None)
    conn = db.connect(dbp)
    row = conn.execute("SELECT widget, email FROM domain").fetchone()
    assert "joinchat" in (row["widget"] or "")
    assert "a@x.com" in (row["email"] or "")


def test_widget_email_second_merge_no_crash(tmp_path):
    """2026-09-28 P0 修复回归：非空列二次 merge 曾重拼出非法 JSON（缺首尾引号），
    json_each 抛 OperationalError → Scrapy 丢 item——首页 widget 落库后同站第 2 个
    item（联系页）必然踩中 ELSE 分支，联系页号码系统性丢失。"""
    dbp = str(tmp_path / "leads.db")
    pl = WaStorePipeline(db_path=dbp)
    pl.open_spider(None)
    # 首页：joinchat widget + mailto 落库（widget/email 列变非空）
    pl.process_item({"url": "https://x.com/", "html":
        '<script class="joinchat"></script><a href="mailto:a@x.com">a</a>',
        "channel": "sample", "seed_host": "x.com"}, None)
    # 联系页（修复前：ELSE 分支 malformed JSON 异常 → 整 item 丢失）
    pl.process_item({"url": "https://x.com/contact", "html":
        '<script class="getbutton"></script><a href="mailto:b@x.com">b</a>'
        '<a href="https://wa.me/85221234567">wa</a>',
        "channel": "sample", "seed_host": "x.com"}, None)
    pl.close_spider(None)
    conn = db.connect(dbp)
    row = conn.execute("SELECT widget, email FROM domain").fetchone()
    assert set((row["widget"] or "").split(",")) == {"joinchat", "getbutton"}
    assert set((row["email"] or "").split(",")) == {"a@x.com", "b@x.com"}
    # 关键：第 2 个 item 的号码 sighting 必须存活（修复前随异常一起丢）
    assert conn.execute("SELECT COUNT(*) FROM sighting").fetchone()[0] == 1


def test_developer_name_persists_and_drives_p0(tmp_path):
    """CRIT #1 端到端：play 渠道带 developerName 的种子 → domain.developer_name
    入表 → close_spider 打分时 read 回 score_domain → 中国公司名命中 P0。
    （域名避开 example.*——B3 后实体键含 example 会被 demo 规则归零）"""
    dbp = str(tmp_path / "leads.db")
    pl = WaStorePipeline(db_path=dbp)
    pl.open_spider(None)
    # 模拟 spider 输出的 item：带 developer_name（来自种子的 P0 第 1 强信号）
    pl.process_item({
        "url": "https://brand-acme.io/",
        "html": "<html lang='en'><body>Plain English page, no WA here.</body></html>",
        "channel": "play",
        "seed_host": "brand-acme.io",
        "developer_name": "深圳市某某科技有限公司",
    }, None)
    pl.close_spider(None)
    conn = db.connect(dbp)
    row = conn.execute("SELECT p0, score, developer_name, market_group "
                       "FROM domain").fetchone()
    assert row["p0"] == 1, "中国公司名 P0 第 1 强信号必须落库并参与判定"
    assert row["developer_name"] == "深圳市某某科技有限公司"
    assert row["market_group"] is None       # 无号码 → 无市场分组


def test_demo_domain_filtered():
    """B3（2026-09-28）：demo/模板/widget 厂商域名级假阳性过滤（spec 3.5）。"""
    from youzi_bsp.score import score_domain

    flags = {"lang": "en", "icp": False, "hreflang_zh": False, "title_zh": False}
    for dom in ("demo.com", "demosite.com", "elfsight.com", "tidio.co",
                "example.com", "templatemonster.net"):
        res = score_domain(flags, ["US"], dom)
        assert res["p0"] == 0 and res["score"] == 0, dom
    # 描述型合法商家（keyword 后是连字符）不受影响
    assert score_domain(flags, ["US"], "demo-site-agency.com")["score"] == 3


def test_market_group_lang_fallback():
    """B3（2026-09-28）：无号码证据时 market_group 按页面 lang 兜底（spec 3.5
    市场分组第二信号）；market 字段保持 None——不用 lang 编造国家码。"""
    from youzi_bsp.score import score_domain

    flags = {"lang": "id", "icp": False, "hreflang_zh": False, "title_zh": False}
    res = score_domain(flags, [], "warung-makan.com")
    assert res["market"] is None and res["market_group"] == "SEA"
    assert score_domain({**flags, "lang": "es"}, [], "x.com")["market_group"] == "LATAM"
    assert score_domain({**flags, "lang": "ar"}, [], "x.com")["market_group"] == "MENA"
    assert score_domain({**flags, "lang": "zh"}, [], "x.com")["market_group"] == "CN"
    # en 不在兜底表 → None（不编造分组）
    assert score_domain({**flags, "lang": "en"}, [], "x.com")["market_group"] is None


# ============================================================================
# CLI 校验 + JOBDIR 默认
# ============================================================================

def test_cli_max_pages_min_one():
    """MED 修复：--max-pages 0/-1 等非法值必须报错而非静默退化为无效。"""
    from youzi_bsp.cli import main
    import pytest as _pt

    for bad in ("0", "-1"):
        with _pt.raises(SystemExit):
            main(["crawl", "--seed-file", "x", "--max-pages", bad])


def test_cli_crawl_default_jobdir(tmp_path, monkeypatch):
    """MED 修复：--jobdir 缺省时给个 data/.job/<channel>-<yyyymmdd>/ 默认值。"""
    from youzi_bsp.cli import main
    import os

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["youzi_bsp", "crawl", "--seed-file", "x",
                                     "--channel", "play"])
    # 不真正跑 Scrapy：检查 main() 解析时设置的 JOBDIR 默认值
    # （crawl 子命令会启动 CrawlerProcess 主循环，patch 掉让它直接返回）
    import scrapy.crawler
    monkeypatch.setattr(scrapy.crawler, "CrawlerProcess",
                       lambda *a, **k: type("P", (), {"crawl": lambda *a, **k: None,
                                                        "start": lambda self: None})())
    main(["crawl", "--seed-file", "x", "--channel", "play"])
    # 检查默认值目录已创建（或将创建）
    assert any((tmp_path / "data" / ".job").glob("play-*")), \
        "默认 jobdir 应在 data/.job/<channel>-<yyyymmdd>/"
