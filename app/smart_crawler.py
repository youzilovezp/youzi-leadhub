"""智能爬虫策略引擎（Smart Crawler Scheduler，2026-09-30）。

设计目标：从「拍脑袋选择 channel」升级为「数据驱动的 ROI 调度」。
核心指标：
- hit_rate = hits / attempts（累计入库 entity 占总尝试 URL）
- error_rate = errors / attempts
- score = hit_rate / (1 + error_rate × 10)（高命中 + 低错误 = 高分）

冷却机制：连续 5+ 次 errored → 5 分钟冷却，避免在已死的 channel 上浪费预算。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable


# 调度参数（智能引擎的可调常量——后续按线上数据调）
BACKOFF_THRESHOLD = 5       # 连续错误几次触发冷却
BACKOFF_DURATION_MINUTES = 5  # 冷却时长
ERROR_PENALTY_WEIGHT = 10    # error_rate 在分母上的放大系数
WINDOW_LIMIT = 200           # 只看最近 N 次 attempts（滚动）


@dataclass
class ChannelStrategy:
    """单个 channel 的智能调度建议。"""
    channel: str
    score: float               # ROI 评分（越大越该爬）
    hit_rate: float            # 累计命中率
    error_rate: float           # 错误率
    consecutive_errors: int   # 当前错误连击
    backoff_until: str | None  # 冷却到期时刻
    last_hit_at: str | None    # 上次入库时间
    recommendation: str         # 'crawl_now' | 'crawl_slow' | 'backoff'
    reason: str                 # 给用户看的中文解释


def compute_strategy(channel: str, stats: dict | None,
                    now: datetime | None = None) -> ChannelStrategy:
    """从单 channel 的 stats 算策略——纯函数无副作用。"""
    now = now or datetime.now(timezone.utc)

    # 默认值（从未爬过）
    attempts = stats.get("attempts", 0) if stats else 0
    hits = stats.get("hits", 0) if stats else 0
    phones = stats.get("phones", 0) if stats else 0
    errors = stats.get("errors", 0) if stats else 0
    consec = stats.get("consecutive_errors", 0) if stats else 0
    backoff_until = stats.get("backoff_until") if stats else None
    last_hit_at = stats.get("last_hit_at") if stats else None

    # 命中率（防止 0 attempts 时除零）
    hit_rate = hits / attempts if attempts > 0 else 0.5  # 默认 50% 假设
    error_rate = errors / attempts if attempts > 0 else 0
    # 综合 ROI 评分（高命中 + 低错误 → 高分）
    score = hit_rate / (1 + error_rate * ERROR_PENALTY_WEIGHT)

    # 冷却期检查
    in_backoff = False
    if backoff_until:
        try:
            backoff_time = datetime.fromisoformat(backoff_until.replace("Z", "+00:00"))
            if backoff_time > now:
                in_backoff = True
        except (ValueError, TypeError):
            pass

    # 决策：冷却 → backoff；错误率高 → crawl_slow；否则 → crawl_now
    # 2026-09-30 修复：consec≥5 且 backoff_until 已写过（哪怕已过期）= 冷却已服务过
    # ——必须放行恢复（否则 consec 只在成功时重置、渠道又永远不被爬 → 永久锁死：
    # play 20% 命中好渠道被锁 40 分钟实锤）。恢复后一次成功 consec 即归零自愈。
    if in_backoff:
        recommendation = "backoff"
        reason = f"冷却中（{consec} 次错误连击）"
    elif consec >= BACKOFF_THRESHOLD and not backoff_until:
        recommendation = "backoff"
        reason = f"{consec} 次连续错误，应触发冷却"
    elif error_rate > 0.5 and attempts > 10:
        recommendation = "crawl_slow"
        reason = f"错误率 {error_rate:.0%} 过高，建议降低并发"
    elif hit_rate == 0 and attempts > 20:
        recommendation = "crawl_slow"
        reason = f"已爬 {attempts} 个 0 入库，信号失效"
    else:
        recommendation = "crawl_now"
        reason = f"命中率 {hit_rate:.0%}（{hits}/{attempts}）"

    return ChannelStrategy(
        channel=channel,
        score=score,
        hit_rate=hit_rate,
        error_rate=error_rate,
        consecutive_errors=consec,
        backoff_until=backoff_until,
        last_hit_at=last_hit_at,
        recommendation=recommendation,
        reason=reason,
    )


def rank_channels(channels: Iterable[str], stats_map: dict[str, dict],
                 now: datetime | None = None) -> list[ChannelStrategy]:
    """所有 channel 排序——按 score DESC，最优在前。"""
    return sorted(
        (compute_strategy(c, stats_map.get(c), now=now) for c in channels),
        key=lambda s: s.score,
        reverse=True,
    )


def pick_best_channel(channels: Iterable[str], stats_map: dict[str, dict],
                      now: datetime | None = None) -> ChannelStrategy | None:
    """选当前 ROI 最高且不在冷却中的 channel——给 'smart' 模式用。"""
    ranked = rank_channels(channels, stats_map, now=now)
    for s in ranked:
        if s.recommendation != "backoff":
            return s
    return ranked[0] if ranked else None  # 全部冷却 → 选评分最高的兜底


# ============================================================================
# L1: Per-seed URL scoring（2026-09-30）
# ============================================================================
# 思路：domain 表里已有 score 列（爬后写的 0-100 综合分）——可作为「该域历史产出」
# 的代理：score 高的域过去产出过 lead（WA + ICP + P0 等强信号），新 seed 命中它的
# 子域/相似域也可能高产。把 start_urls 按历史 yield_score 排序，高分优先爬：
# 在相同 crawl budget 下优先验证「已知有钱」的域。
#
# 这是 lazy index——不存新表，直接 JOIN domain 表排序。如果域没爬过（domain
# 表里没有），默认 1.0（中位值）；score=0 视为「已知零产」，排最后。

DEFAULT_YIELD_SCORE = 1.0  # 中位默认（无历史数据时）


def rank_seeds_by_yield(conn, urls: list[str]) -> list[str]:
    """按历史 yield_score 排序 seed URLs（高分优先）。

    - JOIN domain 表：取 entity_key 的 score
    - 没记录的 entity 用 DEFAULT_YIELD_SCORE
    - score=0 的实体（已知 0 产出）排最后
    - 稳定排序：相同 score 用原顺序保证确定性
    """
    if not urls:
        return []
    # 提取 entity_key（normal 后的 host → entity_key）
    from app.normalize import entity_key as _ek
    keys = []
    key_for_url: dict[str, str] = {}
    for u in urls:
        try:
            from urllib.parse import urlsplit
            host = (urlsplit(u).hostname or '').lower()
            k = _ek(host) if host else ''
        except Exception:
            k = ''
        keys.append(k)
        if k:
            key_for_url[k] = u

    if not keys:
        return list(urls)

    # 批量查 score
    placeholders = ','.join('?' * len(set(k for k in keys if k)))
    if not placeholders:
        return list(urls)
    unique_keys = list({k for k in keys if k})
    rows = conn.execute(
        f"SELECT entity_key, score FROM domain WHERE entity_key IN ({placeholders})",
        unique_keys,
    ).fetchall()
    score_map = {r['entity_key']: (r['score'] or 0) for r in rows}

    def _sort_key(u: str) -> tuple:
        k = ''
        try:
            from urllib.parse import urlsplit
            host = (urlsplit(u).hostname or '').lower()
            from app.normalize import entity_key as _ek
            k = _ek(host) if host else ''
        except Exception:
            pass
        # 优先级：高 yield > 有数据 > score=0 排最后
        score = score_map.get(k, DEFAULT_YIELD_SCORE)
        # score=0 → bucket 1（最后），有 score → bucket 0（前面）
        bucket = 1 if score == 0 else 0
        return (-score, bucket)

    return sorted(urls, key=_sort_key)


# ============================================================================
# L2: Auto-expansion（2026-09-30）
# ============================================================================
# 思路：channel 种池耗尽时（seen ≈ total）自动调 seeds.py 加新种子——无需 admin 手动。
# 触发条件：
#   1. smart crawl 调用时检测 unseen < 100（即将耗尽）
#   2. 后台 cron job（可选，本次不实现）
# 实现：admin endpoint + 自动 spawn seeds.py 子进程

import subprocess
import os
import sys


def auto_expand_seed(channel: str, *, country: str = '', limit: int = 500,
                     out_file: str | None = None,
                     cwd: str | None = None) -> dict:
    """自动给 channel 加新种子——调 seeds.py spawn 子进程。

    返回：dict 含 cmd/pid/started_at/log 字段（与 _spawn 一致风格）。
    """
    import time
    if out_file is None:
        out_file = f"data/seeds-{channel}.txt"
    if cwd is None:
        cwd = os.getcwd()

    cmd = [sys.executable, "-m", "app", "seed", channel,
           "--limit", str(limit), "--out", out_file]
    if country:
        cmd += ["--country", country]
    # play 渠道需要 --category 才有意义；play 默认 BUSINESS
    if channel == "play":
        cmd += ["--category", "BUSINESS,SHOPPING,COMMUNICATION"]
    # log 路径要绝对路径——Popen 的 cwd= 只影响子进程 cwd，不影响当前进程 PWD
    # （防止不同 cwd 调用时日志写到错位置）
    log_dir = os.path.join(cwd, "data", ".crawl-logs") if cwd else "data/.crawl-logs"
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"expand-{channel}-{time.strftime('%Y%m%d-%H%M%S')}.log")
    log_handle = open(log_file, "a", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=log_handle,
                            stderr=subprocess.STDOUT, start_new_session=True)
    log_handle.close()
    return {
        "cmd": cmd,
        "pid": proc.pid,
        "started_at": time.time(),
        "log": log_file,
        "channel": channel,
        "country": country,
        "limit": limit,
    }


# ============================================================================
# L3: Per-channel AutoThrottle（2026-09-30）
# ============================================================================
# 思路：根据 ROI 动态设 AUTOTHROTTLE_TARGET_CONCURRENCY：
#   - 高命中 (>30%) → 激进：target=3.0, delay=0.3s
#   - 中命中 (10-30%) → 中等：target=2.0, delay=0.5s
#   - 低命中 (<10%) → 保守：target=1.0, delay=1.0s（信号失效，避免浪费）

@dataclass
class ThrottleTuning:
    """单个 channel 的 Scrapy AutoThrottle 参数。"""
    channel: str
    target_concurrency: float
    download_delay: float
    autothrottle_enabled: bool = True


def compute_throttle(channel: str, stats: dict | None) -> ThrottleTuning:
    """根据 ROI 动态调 AutoThrottle——高分快爬、低分慢爬。"""
    if not stats or stats.get("attempts", 0) < 10:
        # 没数据用默认（与 settings.py 一致）
        return ThrottleTuning(channel, target_concurrency=1.5, download_delay=0.5)

    hit_rate = stats.get("hits", 0) / max(1, stats["attempts"])
    consec_errors = stats.get("consecutive_errors", 0)
    in_backoff = False
    try:
        if stats.get("backoff_until"):
            from datetime import datetime
            bt = datetime.fromisoformat(stats["backoff_until"].replace("Z", "+00:00"))
            if bt > datetime.now(timezone.utc):
                in_backoff = True
    except Exception:
        pass

    if in_backoff:
        return ThrottleTuning(channel, target_concurrency=0.5, download_delay=2.0)

    if hit_rate > 0.3:
        return ThrottleTuning(channel, target_concurrency=3.0, download_delay=0.3)
    if hit_rate > 0.1:
        return ThrottleTuning(channel, target_concurrency=2.0, download_delay=0.5)
    return ThrottleTuning(channel, target_concurrency=1.0, download_delay=1.0)
