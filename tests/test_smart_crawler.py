"""智能爬虫策略引擎测试（2026-09-30）。

覆盖：纯函数（compute_strategy / pick_best_channel / rank_channels），
不需要 DB / 网络——可在 CI 直接跑。
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.smart_crawler import (
    BACKOFF_THRESHOLD,
    ChannelStrategy,
    compute_strategy,
    pick_best_channel,
    rank_channels,
)


def check(name, ok, detail=""):
    if ok:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}: {detail}")
        raise AssertionError(f"{name}: {detail}")


# === 纯函数: compute_strategy ===

def test_compute_strategy_default_no_stats():
    """从未爬过：score=0.5（默认命中率假设），推荐 crawl_now。"""
    s = compute_strategy("play", None)
    check("无 stats 默认 score=0.5", abs(s.score - 0.5) < 1e-9)
    check("recommendation=crawl_now", s.recommendation == "crawl_now")


def test_compute_strategy_high_hit_low_error():
    """命中率 55% + 错误率 5% → score=0.367 高分 → crawl_now。"""
    stats = {"attempts": 100, "hits": 55, "errors": 5}
    s = compute_strategy("osm", stats)
    check("osm score 高分", s.score > 0.3, f"got {s.score:.3f}")
    check("hit_rate=55%", abs(s.hit_rate - 0.55) < 1e-9)
    check("recommendation=crawl_now", s.recommendation == "crawl_now")


def test_compute_strategy_zero_hits_high_errors():
    """命中率 0% + 错误率高 → score 低 → crawl_slow（信号失效）。"""
    stats = {"attempts": 60, "hits": 0, "errors": 5}
    s = compute_strategy("myshopify", stats)
    check("myshopify score=0", s.score == 0.0)
    check("hit_rate=0%", s.hit_rate == 0.0)
    check("recommendation=crawl_slow（信号失效）", s.recommendation == "crawl_slow")


def test_compute_strategy_consecutive_errors_triggers_backoff():
    """连续 5 次错误 → backoff。"""
    stats = {"attempts": 100, "hits": 50, "errors": 50, "consecutive_errors": 5}
    s = compute_strategy("play", stats)
    check("5 次错误连击 → backoff", s.recommendation == "backoff")


def test_compute_strategy_error_resets_consecutive():
    """errors=0 时 consecutive_errors 应被传入方重置为 0（纯函数看传入的 stats）。"""
    # 模拟 DB 中 DO UPDATE 已重置后的状态：consecutive_errors=0 + errors=0
    stats = {"attempts": 100, "hits": 50, "errors": 0, "consecutive_errors": 0}
    s = compute_strategy("play", stats)
    check("errors=0 + consecutive_errors=0 → 不触发 backoff", s.recommendation != "backoff")


def test_compute_strategy_backoff_from_explicit_consecutive():
    """consecutive_errors 字段已 ≥5 时直接 backoff（即使 errors 字段是旧的快照）。"""
    stats = {"attempts": 100, "hits": 50, "errors": 0, "consecutive_errors": 10}
    s = compute_strategy("play", stats)
    # consecutive_errors=10 → BACKOFF_THRESHOLD=5 → 触发 backoff
    check("consecutive_errors≥5 → backoff", s.recommendation == "backoff")


# === rank_channels: 排序 ===

def test_rank_channels_orders_by_score_desc():
    """ranked 应按 score DESC：高分 channel 在前。"""
    stats_map = {
        "play":       {"attempts": 80, "hits": 16, "errors": 10, "consecutive_errors": 1},   # 0.089
        "osm":        {"attempts": 100, "hits": 55, "errors": 5},                          # 0.367
        "myshopify":  {"attempts": 60, "hits": 0, "errors": 5},                            # 0
    }
    ranked = rank_channels(("play", "osm", "myshopify"), stats_map)
    order = [s.channel for s in ranked]
    check("ranked[0]=osm（最高分）", order[0] == "osm")
    check("ranked[1]=play", order[1] == "play")
    check("ranked[2]=myshopify（最低分）", order[2] == "myshopify")


# === pick_best_channel: 选最优 ===

def test_pick_best_picks_highest_score():
    """选 ROI 最高的 channel（按 score 排序）。"""
    stats_map = {
        "play":       {"attempts": 80, "hits": 16, "errors": 10},
        "osm":        {"attempts": 100, "hits": 55, "errors": 5},  # best
    }
    best = pick_best_channel(("play", "osm"), stats_map)
    check("best.channel=osm", best.channel == "osm")


def test_pick_best_skips_backoff():
    """全 cooling 时 fallback 到排序第一（避免无 channel 可用）。"""
    now = datetime.now(timezone.utc)
    future = (now + timedelta(minutes=5)).isoformat()
    stats_map = {
        "osm":  {"attempts": 100, "hits": 55, "errors": 5, "consecutive_errors": 0, "backoff_until": future},
        "play": {"attempts": 80, "hits": 16, "errors": 10, "consecutive_errors": 0, "backoff_until": future},
    }
    # 所有 channel 都在 backoff → 选评分最高
    best = pick_best_channel(("osm", "play"), stats_map)
    check("全 backoff → 选评分最高（osm）", best.channel == "osm")


def test_compute_strategy_backoff_recovery_after_cooldown_served():
    """2026-09-30 回归：冷却到期后必须恢复可爬。

    旧逻辑 consec>=5 恒 backoff，而 consec 只在成功时重置、被锁渠道永远不爬
    → 永久锁死（play 20% 命中渠道被锁实锤）。backoff_until 已写过且过期 = 冷却已服务。
    """
    from datetime import datetime, timedelta, timezone
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    stats = {"attempts": 80, "hits": 16, "errors": 10,
             "consecutive_errors": 6, "backoff_until": past}
    s = compute_strategy("play", stats)
    check("冷却已过期 → 不再 backoff（恢复试探）", s.recommendation != "backoff",
          f"got {s.recommendation}")

    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    stats2 = {**stats, "backoff_until": future}
    s2 = compute_strategy("play", stats2)
    check("冷却未到期 → 仍 backoff", s2.recommendation == "backoff")


# ============================================================
# 2026-09-30 审计回归：backoff 时间解析（此前冷却在生产格式下全死）
# ============================================================
def test_parse_backoff_until_formats():
    """SQLite datetime() 的 naive 格式与 ISO 格式统一解析为 aware UTC。

    旧代码 fromisoformat 后直接与 aware now 比较——naive 格式（生产落库格式）
    必抛 TypeError 被吞 → in_backoff 恒 False。
    """
    from app.smart_crawler import parse_backoff_until
    naive = parse_backoff_until("2026-09-30 15:35:00")  # SQLite datetime() 输出
    check("naive 格式 → aware UTC", naive is not None and naive.tzinfo is not None)
    iso = parse_backoff_until("2026-09-30T15:35:00Z")
    check("ISO Z 格式 → aware UTC",
          iso is not None and iso.utcoffset().total_seconds() == 0)
    check("空/垃圾 → None",
          parse_backoff_until(None) is None and parse_backoff_until("garbage") is None)


def test_compute_strategy_honors_sqlite_naive_backoff():
    """真实生产格式（SQLite naive 字符串）的未到期冷却必须触发 backoff。"""
    future = (datetime.now(timezone.utc) + timedelta(minutes=4)).strftime(
        "%Y-%m-%d %H:%M:%S")
    s = compute_strategy("play", {"attempts": 10, "hits": 2, "errors": 0,
                                  "consecutive_errors": 6,
                                  "backoff_until": future})
    check("SQLite naive 未到期 → backoff", s.recommendation == "backoff", s.reason)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime(
        "%Y-%m-%d %H:%M:%S")
    s2 = compute_strategy("play", {"attempts": 10, "hits": 2, "errors": 0,
                                   "consecutive_errors": 6,
                                   "backoff_until": past})
    check("SQLite naive 已过期 → 放行恢复", s2.recommendation != "backoff")


def test_compute_throttle_sqlite_naive_backoff_slows():
    """冷却中（naive 格式）throttle 必须收敛到最保守档。"""
    from app.smart_crawler import compute_throttle
    future = (datetime.now(timezone.utc) + timedelta(minutes=4)).strftime(
        "%Y-%m-%d %H:%M:%S")
    t = compute_throttle("play", {"attempts": 50, "hits": 5, "errors": 1,
                                  "consecutive_errors": 6,
                                  "backoff_until": future})
    check("冷却中 → target 0.5 / delay 2.0",
          t.target_concurrency == 0.5 and t.download_delay == 2.0,
          f"got {t.target_concurrency}/{t.download_delay}")
