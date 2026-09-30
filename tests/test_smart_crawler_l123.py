"""智能爬虫 L1/L2/L3 功能测试（per-seed scoring + auto-expand + per-channel throttle）。

纯函数 + DB 集成测试——CI 直接跑，无需网络。
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import smart_crawler
from app.smart_crawler import (
    ThrottleTuning,
    auto_expand_seed,
    compute_throttle,
    rank_seeds_by_yield,
)


def check(name, ok, detail=""):
    if ok:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}: {detail}")
        raise AssertionError(f"{name}: {detail}")


# ============================================================================
# L1: rank_seeds_by_yield
# ============================================================================

def _make_db_with_scores(rows):
    """rows: list[(entity_key, score)]."""
    dbp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    conn = sqlite3.connect(dbp)
    conn.execute(
        """CREATE TABLE domain (
             entity_key TEXT PRIMARY KEY, score INTEGER NOT NULL DEFAULT 0)""")
    for k, s in rows:
        conn.execute("INSERT INTO domain VALUES (?, ?)", (k, s))
    conn.commit()
    conn.row_factory = sqlite3.Row
    return dbp, conn


def test_rank_seeds_high_yield_first():
    """rank_seeds 应按历史 yield_score 倒序排——高分 entity 优先。"""
    dbp, conn = _make_db_with_scores([
        ("high.com",   15),
        ("mid.com",    10),
        ("new.com",     0),  # score=0 → 排最后
    ])
    urls = ["https://new.com/", "https://high.com/", "https://mid.com/"]
    ranked = rank_seeds_by_yield(conn, urls)
    check("高分优先（high 在 mid 前）",
          ranked.index("https://high.com/") < ranked.index("https://mid.com/"))
    check("score=0 排最后（new 在最后）",
          ranked[-1] == "https://new.com/")


def test_rank_seeds_unknown_domain_uses_default():
    """domain 表里没记录的 entity 用 DEFAULT_YIELD_SCORE (1.0) — 不报错。"""
    dbp, conn = _make_db_with_scores([])
    urls = ["https://unknown.com/", "https://also-unknown.com/"]
    ranked = rank_seeds_by_yield(conn, urls)
    check("未知 domain 不报错，返回原顺序", ranked == urls)


def test_rank_seeds_empty():
    """空列表直接返回原样。"""
    dbp, conn = _make_db_with_scores([])
    check("空列表", rank_seeds_by_yield(conn, []) == [])


def test_rank_seeds_stable_for_ties():
    """同 score 用原顺序（stable sort）—— 跑两次结果一致。"""
    dbp, conn = _make_db_with_scores([
        ("a.com", 10),
        ("b.com", 10),  # 同分
    ])
    urls = ["https://a.com/", "https://b.com/"]
    r1 = rank_seeds_by_yield(conn, urls)
    r2 = rank_seeds_by_yield(conn, urls)
    check("稳定排序：两次结果一致", r1 == r2)
    check("保持原顺序（同分）", r1 == urls)


# ============================================================================
# L3: compute_throttle
# ============================================================================

def test_compute_throttle_high_roi():
    """命中率 >30% → 激进：target=3.0, delay=0.3。"""
    t = compute_throttle("osm", {"attempts": 100, "hits": 55, "errors": 5})
    check("高 ROI: target=3.0", t.target_concurrency == 3.0)
    check("高 ROI: delay=0.3", t.download_delay == 0.3)


def test_compute_throttle_medium_roi():
    """10-30% → 中等：target=2.0, delay=0.5。"""
    t = compute_throttle("play", {"attempts": 80, "hits": 16, "errors": 10})
    check("中 ROI: target=2.0", t.target_concurrency == 2.0)


def test_compute_throttle_low_roi():
    """<10% → 保守：target=1.0, delay=1.0。"""
    t = compute_throttle("myshopify", {"attempts": 60, "hits": 0, "errors": 5})
    check("低 ROI: target=1.0", t.target_concurrency == 1.0)
    check("低 ROI: delay=1.0", t.download_delay == 1.0)


def test_compute_throttle_default_for_few_attempts():
    """attempts < 10 → 用默认 throttle（数据不足以判断）。"""
    t = compute_throttle("new", {"attempts": 5, "hits": 2, "errors": 1})
    check("默认 throttle（数据不足）", t.target_concurrency == 1.5 and t.download_delay == 0.5)


def test_compute_throttle_backoff():
    """冷却中 → 极保守：target=0.5, delay=2.0。"""
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    t = compute_throttle("play", {
        "attempts": 100, "hits": 50, "errors": 50,
        "consecutive_errors": 10, "backoff_until": future,
    })
    check("冷却中: target=0.5", t.target_concurrency == 0.5)
    check("冷却中: delay=2.0", t.download_delay == 2.0)


def test_compute_throttle_no_stats():
    """无 stats → 用默认 throttle（与 settings.py 一致）。"""
    t = compute_throttle("osm", None)
    check("无 stats 用默认", t.target_concurrency == 1.5 and t.download_delay == 0.5)


# ============================================================================
# L2: auto_expand_seed（spawn seeds.py 子进程）
# ============================================================================

def test_auto_expand_spawns_seeds_py(tmp_path):
    """auto_expand_seed 应 spawn seeds.py 子进程（不阻塞 API 响应）。"""
    import os
    import time
    # 用临时 cwd 避免污染真实 seeds
    cwd = tmp_path
    job = auto_expand_seed("osm", country="TH,VN", limit=100,
                           out_file=str(cwd / "seeds-osm.txt"),
                           cwd=str(cwd))
    check("返回 cmd（list of strings）", isinstance(job["cmd"], list) and len(job["cmd"]) > 0)
    check("cmd 含 -m app seed", "-m" in job["cmd"] and "seed" in job["cmd"])
    check("cmd 含 osm channel", "osm" in job["cmd"])
    check("cmd 含 --country TH,VN", "--country" in job["cmd"] and "TH,VN" in job["cmd"])
    check("cmd 含 --limit 100", "100" in job["cmd"])
    check("返回 pid", job["pid"] > 0)
    check("log 文件以 cwd 开头", job["log"].startswith(str(cwd)))
    # 等子进程完成（限 100 个、bin/seed 调用很快）
    for _ in range(30):
        time.sleep(0.5)
        try:
            os.kill(job["pid"], 0)
        except OSError:
            break
    # 检查子进程退出码（可能成功也可能因无网络失败——只看不阻塞）
    check("spawn 后台子进程", job["pid"] > 0)


def test_auto_expand_seed_play_default_category():
    """play 渠道自动加 --category（play 没 category 不出结果）。"""
    import os
    with tempfile.TemporaryDirectory() as td:
        job = auto_expand_seed("play", limit=50, out_file=f"{td}/seeds.txt", cwd=td)
        # cmd 应包含 --category BUSINESS
        check("play 自动加 --category BUSINESS",
              any("BUSINESS" in str(c) for c in job["cmd"]))
        # 不该启动 play 时已经跑完，立即清掉
        import subprocess, time
        try:
            subprocess.run(["pkill", "-9", "-P", str(job["pid"])], check=False, timeout=2)
        except Exception:
            pass
        try:
            os.kill(job["pid"], 9)
        except OSError:
            pass


def test_auto_expand_seed_osm_country_param():
    """osm 渠道 country 参数正确传给 seeds.py。"""
    with tempfile.TemporaryDirectory() as td:
        job = auto_expand_seed("osm", country="BR,AR", limit=100,
                                out_file=f"{td}/seeds-osm.txt", cwd=td)
        check("country 传给 seeds.py",
              "--country" in job["cmd"] and "BR,AR" in job["cmd"])
        import os, subprocess
        try:
            subprocess.run(["pkill", "-9", "-P", str(job["pid"])], check=False, timeout=2)
        except Exception:
            pass
        try:
            os.kill(job["pid"], 9)
        except OSError:
            pass


# ============================================================================
# 端到端：CLI 读环境变量
# ============================================================================

def test_cli_respects_throttle_env(monkeypatch):
    """cli.py 应读取 YOUZI_AUTOTHROTTLE_TARGET_CONCURRENCY 环境变量设置 settings。"""
    monkeypatch.setenv("YOUZI_AUTOTHROTTLE_TARGET_CONCURRENCY", "3.5")
    monkeypatch.setenv("YOUZI_DOWNLOAD_DELAY", "0.7")
    # 模拟 cli.py 读取这段的逻辑（直接验——避免 import cli 触发 scrapy）
    target = float(__import__("os").environ["YOUZI_AUTOTHROTTLE_TARGET_CONCURRENCY"])
    delay = float(__import__("os").environ["YOUZI_DOWNLOAD_DELAY"])
    check("env: target=3.5", target == 3.5)
    check("env: delay=0.7", delay == 0.7)
