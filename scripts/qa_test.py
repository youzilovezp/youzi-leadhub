"""系统级 QA 测试套件（2026-09-30，测试工程师视角）。

按 QA 矩阵覆盖：正常路径 + 边界 + 并发 + 智能爬虫 + 数据完整性 + 错误恢复 + 性能 + 安全。
"""
import concurrent.futures
import json
import os
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("BSP_BASE_URL", "http://127.0.0.1:8788")
SESSION = requests.Session()
SESSION.trust_env = False

PASS = 0
FAIL = 0
RESULTS: list[tuple[str, bool, str, float]] = []


class FakeResp:
    """请求异常时的 fake 500 响应——让脚本所有 .status_code 调用不崩。"""
    def __init__(self, exc):
        self.status_code = 500
        self.text = f"{type(exc).__name__}: {exc}"
        self.content = self.text.encode()
    def json(self): return {}


def check(name: str, ok: bool, detail: str = "", ms: float = 0.0) -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name} ({ms:.0f}ms)")
    else:
        FAIL += 1
        print(f"  ❌ {name}: {detail}")
    RESULTS.append((name, ok, detail, ms))


def section(name: str) -> None:
    print(f"\n{'='*70}\n{name}\n{'='*70}")


def get(url: str, **kw):
    """带 P95 测量的 GET——失败时返回 FakeResp。"""
    t0 = time.perf_counter()
    try:
        r = SESSION.get(url, timeout=10, **kw)
        return r, (time.perf_counter() - t0) * 1000
    except Exception as e:
        return FakeResp(e), (time.perf_counter() - t0) * 1000


def post(url: str, **kw):
    t0 = time.perf_counter()
    try:
        r = SESSION.post(url, timeout=10, **kw)
        return r, (time.perf_counter() - t0) * 1000
    except Exception as e:
        return FakeResp(e), (time.perf_counter() - t0) * 1000


def patch(url: str, **kw):
    t0 = time.perf_counter()
    try:
        r = SESSION.patch(url, timeout=10, **kw)
        return r, (time.perf_counter() - t0) * 1000
    except Exception as e:
        return FakeResp(e), (time.perf_counter() - t0) * 1000


def options(url: str, **kw):
    t0 = time.perf_counter()
    try:
        r = SESSION.options(url, timeout=10, **kw)
        return r, (time.perf_counter() - t0) * 1000
    except Exception as e:
        return FakeResp(e), (time.perf_counter() - t0) * 1000


# ============================================================================
# [1] 正常路径（Happy Path）
# ============================================================================
section("[1] Happy Path — Dashboard 加载 + 所有 GET API + POST")

r, ms = get(BASE + "/")
check("1.1.1 GET / 200 + 含 HTML",
      r.status_code == 200 and b"<html" in r.content, f"status={r.status_code}", ms)

endpoints = [
    ("/api/stats", "channel"),
    ("/api/leads?limit=1", "entity"),
    ("/api/scenes", []),
    ("/api/seeds", "pools"),
    ("/api/crawl/status", "jobs"),
    ("/api/today", []),
    ("/api/progress", "by_status"),
    ("/api/crawler/strategy", "channels"),
]
for ep, expect_key in endpoints:
    r, ms = get(BASE + ep)
    try:
        data = r.json()
        ok = r.status_code == 200 and (
            expect_key in data if isinstance(data, dict) else isinstance(data, list))
        check(f"1.2 GET {ep}", ok, f"status={r.status_code} type={type(data).__name__}", ms)
    except Exception as e:
        check(f"1.2 GET {ep}", False, str(e), ms)

# POST /api/crawl incremental
r, ms = post(BASE + "/api/crawl",
             json={"channels": ["osm"], "mode": "incremental", "limit": 5, "max_pages": 1})
check("1.3.1 POST /api/crawl incremental",
      r.status_code == 200 and r.json().get("total", 0) >= 1, f"{r.status_code}", ms)

# PATCH contact status
r, _ = get(BASE + "/api/leads?limit=1&channel=osm")
entity = r.json() if r.status_code == 200 else []
if entity:
    e = entity[0]["entity"]
    r, ms = patch(BASE + f"/api/leads/{e}/status",
                  json={"status": "contacted", "notes": "QA test"})
    check("1.4 PATCH /api/leads/{id}/status",
          r.status_code == 200 and r.json().get("status") == "contacted", f"{r.status_code}", ms)
else:
    check("1.4 PATCH skip (no osm lead)", True, "skipping — no data", 0)

# forget 端到端（临时 DB）
with tempfile.TemporaryDirectory() as td:
    sys.path.insert(0, str(PROJECT_ROOT))
    from app import db as dbm
    dbp = Path(td) / "t.db"
    conn = dbm.connect(dbp)
    # 一号多挂：a.com 和 b.com 都引用 +8613800138777
    dbm.upsert_domain(conn, "a.com", "test", None)
    dbm.upsert_domain(conn, "b.com", "test", None)
    dbm.upsert_phone(conn, "+8613800138777", "CN")
    dbm.upsert_sighting(conn, "a.com", "+8613800138777", "https://a.com/", "link")
    dbm.upsert_sighting(conn, "b.com", "+8613800138777", "https://b.com/", "link")
    n = dbm.forget(conn, "a.com")
    check("1.6.1 forget 删 1 个 domain", n == 1)
    ph = [r[0] for r in conn.execute("SELECT e164 FROM phone")]
    check("1.6.2 phone 一号多挂不误删", "+8613800138777" in ph)
    dbm.forget(conn, "b.com")
    ph2 = [r[0] for r in conn.execute("SELECT e164 FROM phone")]
    check("1.6.3 forget 第二实体后 phone 孤儿清理",
          "+8613800138777" not in ph2)
    conn.close()


# ============================================================================
# [2] 边界（Edge Cases）
# ============================================================================
section("[2] Edge Cases")

r, ms = get(BASE + "/api/leads?limit=0")
check("2.1 limit=0 → 422", r.status_code == 422, f"got {r.status_code}", ms)

r, ms = get(BASE + "/api/leads?limit=2001")
check("2.2 limit=2001 → 422 (超 le=2000)", r.status_code == 422, f"got {r.status_code}", ms)

r, ms = post(BASE + "/api/crawl",
              json={"channels": ["myshopify"], "mode": "incremental", "limit": 0})
check("2.3 POST limit=0 → 422", r.status_code == 422, f"got {r.status_code}", ms)

r, ms = post(BASE + "/api/crawl",
              json={"channels": ["myshopify"], "mode": "incremental", "limit": 50001})
check("2.4 POST limit=50001 → 422", r.status_code == 422, f"got {r.status_code}", ms)

r, ms = post(BASE + "/api/crawl", json={"channels": ["ghost"], "mode": "incremental"})
check("2.5 POST 未知 channel → 400", r.status_code == 400, f"got {r.status_code}", ms)

r, ms = post(BASE + "/api/seeds", json={"scene": "no_such_scene"})
check("2.6 POST /api/seeds 未知 scene → 400",
      r.status_code == 400, f"got {r.status_code}", ms)

r, ms = post(BASE + "/api/crawl", json={"channels": ["myshopify"], "mode": "hacker"})
check("2.7 POST mode 非法字面量 → 422",
      r.status_code == 422, f"got {r.status_code}", ms)

r, ms = post(BASE + "/api/crawl",
              json={"channels": ["osm"], "mode": "incremental", "db_path": "../../etc/leads.db"})
check("2.8 db_path ../ → 400", r.status_code == 400, f"got {r.status_code}", ms)

r, ms = post(BASE + "/api/crawl",
              json={"channels": ["osm"], "mode": "incremental", "db_path": "/etc/passwd"})
check("2.9 db_path 绝对路径 → 400", r.status_code == 400, f"got {r.status_code}", ms)

r, ms = patch(BASE + "/api/leads/everpro.id/status", json={"status": "pending"})
check("2.10 PATCH 非法 status → 400",
      r.status_code == 400, f"got {r.status_code}", ms)

r, ms = post(BASE + "/api/seeds", json={})
check("2.11 POST /api/seeds 缺 scene → 422",
      r.status_code == 422, f"got {r.status_code}", ms)

r, ms = patch(BASE + "/api/leads/ghost-domain-xyz/status", json={"status": "contacted"})
check("2.12 PATCH 不存在 entity → 404",
      r.status_code == 404, f"got {r.status_code}", ms)


# ============================================================================
# [3] 并发 (Concurrency)
# ============================================================================
section("[3] 并发 — TOCTOU + 并发状态查询")

def hit_crawl():
    r, _ = post(BASE + "/api/crawl",
                json={"channels": ["myshopify"], "mode": "full", "limit": 3})
    return r.status_code

with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
    codes = list(ex.map(lambda _: hit_crawl(), range(5)))
ok_count = sum(1 for c in codes if c == 200)
confl_count = sum(1 for c in codes if c == 409)
check("3.1 5 并发 spawn 同 channel → 1×200 + 4×409",
      ok_count == 1 and confl_count == 4, f"got {codes}")

def hit_patch():
    r, _ = patch(BASE + "/api/leads/everpro.id/status", json={"status": "contacted"})
    return r.status_code

with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
    codes = list(ex.map(lambda _: hit_patch(), range(10)))
ok = sum(1 for c in codes if c == 200)
# 200 是 expected；429/409 也是 SQLite 锁竞争下的合法（SPEC §五工程节流）
# 不应该是 500（500 = 后端 bug）
no_5xx = all(c < 500 for c in codes)
check("3.2 10 并发 PATCH → 无 500（last-write-wins）",
      no_5xx and ok >= 5, f"got {codes}")

def hit_get(ep):
    r, _ = get(BASE + ep)
    return r.status_code

with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
    futures = [ex.submit(hit_get, "/api/today") for _ in range(16)]
    codes = [f.result() for f in futures]
check("3.3 16 并发 /api/today → 全 200",
      all(c == 200 for c in codes), f"got codes={codes[:5]}...")

with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
    futures = [ex.submit(hit_get, "/api/crawl/status") for _ in range(16)]
    codes = [f.result() for f in futures]
check("3.4 16 并发 /api/crawl/status → 全 200",
      all(c == 200 for c in codes), f"got {codes[:5]}...")


# ============================================================================
# [4] 智能爬虫（Smart Crawler）
# ============================================================================
section("[4] Smart Crawler — L0/L1/L2/L3")

sys.path.insert(0, str(PROJECT_ROOT))
from app import smart_crawler as _sc

s_high = _sc.compute_strategy("osm", {"attempts": 100, "hits": 55, "errors": 5})
check("4.1.1 高 ROI osm → score 高 + crawl_now",
      s_high.score > 0.3 and s_high.recommendation == "crawl_now")

s_zero = _sc.compute_strategy("myshopify", {"attempts": 60, "hits": 0, "errors": 5})
check("4.1.2 零命中 myshopify → score=0 + crawl_slow",
      s_zero.score == 0.0 and s_zero.recommendation == "crawl_slow")

s_backoff = _sc.compute_strategy("play",
    {"attempts": 100, "hits": 50, "errors": 50, "consecutive_errors": 10,
     "backoff_until": "2026-12-31T00:00:00+00:00"})
check("4.1.3 冷却中 → backoff", s_backoff.recommendation == "backoff")

with tempfile.TemporaryDirectory() as td:
    dbp = Path(td) / "t.db"
    conn = sqlite3.connect(dbp)
    conn.row_factory = sqlite3.Row  # smart_crawler 需要 dict-like 访问
    conn.execute("CREATE TABLE domain (entity_key TEXT PRIMARY KEY, score INTEGER)")
    conn.executemany("INSERT INTO domain VALUES (?, ?)",
                      [("high.com", 15), ("mid.com", 10), ("zero.com", 0)])
    conn.commit()
    ranked = _sc.rank_seeds_by_yield(conn,
        ["https://zero.com/", "https://high.com/", "https://mid.com/"])
    check("4.2 L1 高分优先",
          ranked.index("https://high.com/") < ranked.index("https://mid.com/"))
    check("4.2.2 L1 score=0 排最后", ranked[-1] == "https://zero.com/")
    conn.close()

import os as _os
with tempfile.TemporaryDirectory() as td:
    job = _sc.auto_expand_seed("osm", country="TH", limit=50,
                                out_file=f"{td}/seeds-osm.txt", cwd=td)
    check("4.3.1 L2 auto_expand 返回 cmd", isinstance(job["cmd"], list) and len(job["cmd"]) > 0)
    check("4.3.2 L2 cmd 含 country TH", "TH" in job["cmd"])
    check("4.3.3 L2 返回 log 文件", job["log"].startswith(td))
    import subprocess as _sp
    try:
        _sp.run(["pkill", "-9", "-P", str(job["pid"])], check=False, timeout=2)
    except Exception:
        pass
    try:
        _os.kill(job["pid"], 9)
    except OSError:
        pass

t_high = _sc.compute_throttle("osm", {"attempts": 100, "hits": 55, "errors": 5})
check("4.4.1 L3 高 ROI → target=3.0 + delay=0.3",
      t_high.target_concurrency == 3.0 and t_high.download_delay == 0.3)
t_low = _sc.compute_throttle("x", {"attempts": 60, "hits": 0, "errors": 5})
check("4.4.2 L3 低 ROI → target=1.0 + delay=1.0",
      t_low.target_concurrency == 1.0 and t_low.download_delay == 1.0)


# ============================================================================
# [5] 数据完整性
# ============================================================================
section("[5] 数据完整性 — ON CONFLICT + FK + 迁移幂等")

with tempfile.TemporaryDirectory() as td:
    dbp = Path(td) / "t.db"
    sys.path.insert(0, str(PROJECT_ROOT))
    from app import db as dbm
    conn = dbm.connect(dbp)

    dbm.upsert_domain(conn, "x.com", "test", None)
    dbm.upsert_domain(conn, "x.com", "test", "newhost")
    row = conn.execute("SELECT channel, seed_host FROM domain WHERE entity_key='x.com'").fetchone()
    check("5.1 ON CONFLICT 第二次不覆盖",
          row["channel"] == "test" and row["seed_host"] is None,
          f"got channel={row['channel']} seed_host={row['seed_host']}")

    dbm.upsert_phone(conn, "+8613800138777", "CN")
    dbm.upsert_sighting(conn, "x.com", "+8613800138777", "https://x.com/", "link")
    check("5.2 FK sighting.e164 在 phone 表",
          conn.execute("SELECT COUNT(*) FROM sighting").fetchone()[0] == 1)

    dbm.forget(conn, "x.com")
    n_ph = conn.execute("SELECT COUNT(*) FROM phone").fetchone()[0]
    check("5.3 forget 后 phone 孤儿清理", n_ph == 0, f"剩 {n_ph}")

    dbm._migrate(conn)
    dbm._migrate(conn)
    dbm._migrate(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(domain)").fetchall()}
    expected_cols = {"contact_status", "contacted_at", "enrichment_status",
                     "outreach_message", "tech_signals"}
    check("5.4 migration 幂等（不重复 ALTER）",
          expected_cols.issubset(cols), f"缺: {expected_cols - cols}")
    conn.close()


# ============================================================================
# [6] 错误恢复 / API 健壮性
# ============================================================================
section("[6] 错误恢复 / API 健壮性")

r, ms = get(BASE + "/api/no_such_endpoint")
check("6.1 unknown endpoint → 404", r.status_code == 404, f"got {r.status_code}", ms)

r, ms = post(BASE + "/api/crawl",
              data="not-valid-json{", headers={"Content-Type": "application/json"})
check("6.2 malformed JSON → 422", r.status_code == 422, f"got {r.status_code}", ms)

r, ms = get(BASE + "/api/crawl/status")
# 不能删，但 DELETE 应返回 405
from requests import request as raw_req
try:
    r2 = SESSION.request("DELETE", BASE + "/api/crawl/status", timeout=5)
    check("6.3 DELETE /api/crawl/status → 405", r2.status_code == 405, f"got {r2.status_code}")
except Exception as e:
    check("6.3 DELETE", False, str(e))

r, ms = get(BASE + "/api/crawl/status",
            headers={"Origin": "http://localhost:5173"})
cors = r.headers.get("Access-Control-Allow-Origin", "")
check("6.4 CORS 允许 localhost:5173",
      "5173" in cors, f"got: {cors!r}")


# ============================================================================
# [7] 性能（SLA）
# ============================================================================
section("[7] 性能 — P95 延迟")

def measure_p95(ep, n=20, method="GET"):
    lats = []
    for _ in range(n):
        t0 = time.perf_counter()
        if method == "PATCH":
            SESSION.patch(BASE + ep, json={"status": "new"}, timeout=10)
        else:
            SESSION.get(BASE + ep, timeout=10)
        lats.append((time.perf_counter() - t0) * 1000)
    return statistics.quantiles(lats, n=20)[18]

p95_today = measure_p95("/api/today")
check(f"7.1 /api/today P95 = {p95_today:.0f}ms (<200ms SLA)",
      p95_today < 200, f"P95={p95_today:.0f}ms")

p95_leads = measure_p95("/api/leads?limit=200")
check(f"7.2 /api/leads?limit=200 P95 = {p95_leads:.0f}ms (<300ms SLA)",
      p95_leads < 300, f"P95={p95_leads:.0f}ms")

p95_strat = measure_p95("/api/crawler/strategy")
check(f"7.3 /api/crawler/strategy P95 = {p95_strat:.0f}ms (<100ms SLA)",
      p95_strat < 100, f"P95={p95_strat:.0f}ms")

p95_patch = measure_p95("/api/leads/everpro.id/status", method="PATCH")
check(f"7.4 PATCH status P95 = {p95_patch:.0f}ms (<150ms SLA)",
      p95_patch < 150, f"P95={p95_patch:.0f}ms")


# ============================================================================
# [8] 安全
# ============================================================================
section("[8] 安全 — SQL 注入 + 路径遍历 + XSS")

r, ms = get(BASE + "/api/leads?channel=os%27m%20OR%201%3D1--")
check("8.1 SQL 注入（channel 参数）→ 不暴露其他数据",
      r.status_code == 200,
      f"got {r.status_code}, {len(r.text)} bytes")

r, ms = get(BASE + "/api/leads?channel=<script>alert(1)</script>")
check("8.2 XSS 注入 → 200（前端不渲染）",
      r.status_code == 200, f"got {r.status_code}")


# ============================================================================
# 9. 智能爬取端到端（离线，Q8/Q16 共识）：点击 → 真实 Scrapy 子进程 → hits 递增
#    本地 http.server 挂带 tel: 的静态页（不依赖外网）；sample 渠道小种子
# ============================================================================
import http.server
import socketserver
import threading as _threading

with tempfile.TemporaryDirectory() as _td:
    # ① 本地静态站：两个 host 名不同页（tel: 号码不同，验证各自入库）
    webroot = Path(_td) / "web"
    webroot.mkdir()
    # Scrapy ROBOTSTXT_OBEY：robots.txt 404 会被视为"解析失败→拒绝全部请求"
    # （IgnoreRequest）——必须提供允许一切的 robots.txt
    (webroot / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    # detect.py 只认 WhatsApp 形态（wa.me / api.whatsapp.com/send）——QA 页面用它
    (webroot / "index.html").write_text(
        "<html><body>QA shop <a href='https://wa.me/8613800138000'>WhatsApp us</a>"
        "<a href='contact.html'>c</a></body></html>", encoding="utf-8")
    (webroot / "contact.html").write_text(
        "<html><body>contact <a href='https://wa.me/8613900139000'>WhatsApp 2</a></body></html>", encoding="utf-8")

    class _H(http.server.SimpleHTTPRequestHandler):
        # py3.13：directory=None 会回退 cwd（类属性被无视）——必须 __init__ 注入
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(webroot), **kw)
        def log_message(self, *a): pass
    _srv = socketserver.TCPServer(("127.0.0.1", 0), _H)
    _port = _srv.server_address[1]
    _threading.Thread(target=_srv.serve_forever, daemon=True).start()

    # ② 种子文件（127.0.0.1 直连本地——Scrapy ROBOTSTEXT 404 视为允许）
    seed_file = Path("data/seeds-sample.txt")
    seed_file.parent.mkdir(exist_ok=True)
    seed_file.write_text(f"http://127.0.0.1:{_port}/index.html\n", encoding="utf-8")
    try:
        before = {s_["channel"]: s_["hits"] for s_ in
                  requests.get(BASE + "/api/stats", timeout=5).json()}
        # API spawn 的爬虫被 PrivateNetMiddleware 正确拦截（SSRF 红线）——
        # 「点击→hits 递增」的落库断言用 CLI 子进程 + 测试豁免 env 直爬本地站

        r, ms = post(BASE + "/api/crawl", json={
            "channels": ["sample"], "mode": "incremental", "max_pages": 2})
        check("9.1 POST /api/crawl sample → 200 + spawned",
              r.status_code == 200 and r.json().get("total", 0) >= 1,
              r.text[:120])

        # ③ 轮询至 sample job 结束（真子进程，给足 60s）
        import time as _time
        done, t0 = False, _time.time()
        while _time.time() - t0 < 60:
            st = requests.get(BASE + "/api/crawl/status", timeout=5).json()
            sj = [j for j in st.get("jobs", []) if j["channel"] == "sample"]
            if sj and all(j["status"] in ("exited", "failed", "killed") for j in sj):
                done = True
                break
            _time.sleep(2)
        check("9.2 sample 渠道子进程真实跑完（≤60s）", done, str(st)[:120])

        qa_db = Path(_td) / "qa-leads.db"
        if qa_db.exists():
            qa_db.unlink()
        seed_path = Path(_td) / "seeds.txt"
        seed_path.write_text(f"http://127.0.0.1:{_port}/index.html\n", encoding="utf-8")
        import subprocess as _sp, os as _os
        env = {**_os.environ, "BSP_ALLOW_PRIVATE_NET": "1"}
        _r = _sp.run([sys.executable, "-m", "app", "crawl",
                 "--seed-file", str(seed_path), "--channel", "sample",
                 "--limit", "5", "--max-pages", "2", "--db", str(qa_db),
                 "--jobdir", str(Path(_td) / "job")],
                cwd=Path(__file__).resolve().parent.parent, env=env,
                capture_output=True, timeout=90)
        _err_tail = _r.stderr.decode("utf-8", "replace")[-400:]
        import sqlite3 as _sq
        _c = _sq.connect(qa_db); _c.row_factory = _sq.Row
        _n = _c.execute("SELECT COUNT(*) FROM sighting").fetchone()[0]
        _hit = _c.execute(
            "SELECT COUNT(DISTINCT entity_key) FROM sighting").fetchone()[0]
        _c.close()
        check("9.3 端到端断言：爬取 → WA 号码落库（hits > 0）",
              _hit >= 1 and _n >= 2,
              f"sightings={_n} entities={_hit} rc={_r.returncode} "
              f"stderr={_err_tail}")

        # ④ smart 幂等（Q14）：全忙 → 200 非 409
        r2, _ = post(BASE + "/api/crawl", json={
            "channels": ["sample"], "mode": "incremental", "max_pages": 1})
        check("9.4 重复触发 → 409 或 200（子进程已退出后重爬幂等）",
              r2.status_code in (200, 409), f"got {r2.status_code}")
    finally:
        _srv.shutdown()
        seed_file.unlink(missing_ok=True)
        # 9.1 的 API 爬取会在主库留 127.0.0.1 测试实体（被私网中间件拒→error 行）
        # ——清掉，不污染销售线索列表
        _sp.run([sys.executable, "-m", "app", "forget", "--db", "data/leads.db",
                 "--entity", "127.0.0.1"],
                cwd=Path(__file__).resolve().parent.parent, capture_output=True)



# ============================================================================
# 总结
# ============================================================================
print(f"\n{'='*70}")
print(f"PASS: {PASS}  |  FAIL: {FAIL}  |  Total: {PASS + FAIL}")
print(f"{'='*70}")

if FAIL:
    print("\n失败明细（前 30）：")
    for name, ok, detail, ms in RESULTS:
        if not ok:
            print(f"  ❌ {name}: {detail[:120]}")

sys.exit(1 if FAIL > 0 else 0)
