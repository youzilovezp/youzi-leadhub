"""手动触发爬取的 API 端点（POST /api/crawl）。

设计要点（spec §五 工程节流 + §八 MVP 路线）：
- 全渠道/单渠道自由选择（默认 = 全部）
- 后台 subprocess.Popen 异步跑，API 立即返回（不阻塞 HTTP）
- 每个 channel 独立 JOBDIR 防冲突
- 跑批状态用模块级 dict 跟踪（PIDs + 启动时间 + 进程对象）
- 遵守 robots/限速/单站 ≤5 页——复用 cli.py 已配的 Scrapy 设置
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# 2026-09-30 智能爬虫：ROI 查询需要 db 模块
from app import db as _db  # noqa: F401  复用名字避免与函数 db 冲突
from app import smart_crawler as _sc  # 智能调度引擎（per-channel throttle、auto-expand）
from app.normalize import normalize_url

# 路径：API 进程 cwd 是项目根目录（uvicorn 启动方式决定）；爬取需在该 cwd 下
# 跑才能正确解析相对 seed-file / db 路径
_CWD = Path(os.getcwd()).resolve()
_DATA = _CWD / "data"
_LOGS = _CWD / "data" / ".crawl-logs"
_JOBS = _CWD / "data" / ".job"
_LOGS.mkdir(parents=True, exist_ok=True)
_JOBS.mkdir(parents=True, exist_ok=True)

# 2026-09-30 清理：tranco top-1M 是安全域名排序，命中率 0.2%——不是销售线索源
# 保留 cli.py 的 `seed tranco` 命令供手工调试，但 UI/自动流程不再暴露 tranco
# 2026-10-01 移除 myshopify 自动：CDX `*.myshopify.com/*` 数据源天花板 0.6% 命中
# （84% 是 Shopify 默认 hash 子域 → test/abandoned 店），远低于 play 12% 和 osm 52%。
# 智能爬取不再自动 spawn myshopify；手工仍可 `python -m app seed myshopify`
# 单独跑（CLI 兜底），但不在一键列表里。
# 2026-10-01 新增 itunes：iOS App Store RSS 榜 → Lookup sellerUrl（免费无 key，
# 无 Node 依赖）——play 图头部静止（13 次补种 0 新 URL）后的第二 App 渠道主力。
ALL_CHANNELS = ("play", "osm", "itunes", "sample")
# 渠道 → 默认种子文件映射；sample 渠道需用户传 --file 故不在一键列表
SEEDS_DEFAULT: dict[str, str] = {
    "play":      "data/seeds-play.txt",         # 兜底；如有 seeds-play-new.txt 自动优先
    "osm":       "data/seeds-osm.txt",
    "itunes":    "data/seeds-itunes.txt",
}

# S1 fix: 中国大陆/港澳台落独立 CN 组（之前都落 OTHER，对 P0 销售分组误导）
#        销售通常按"中国大陆+港台"做针对性话术（中文母语 + 大陆政策语境）
MARKET_GROUP_CN = {"CN", "HK", "MO", "TW"}


@dataclass
class _Job:
    job_id: str
    channel: str
    pid: int
    started_at: float
    proc: subprocess.Popen | None = None
    log_file: Path | None = None
    # 2026-09-30 扩展：区分"程序崩溃"和"被信号杀死"（系统清理/restart）——信号杀死标 'killed'
    # UX：避免用户看到"失败 (-9)"误以为是程序崩溃，实际是 kill -9 清理的
    status: Literal["created", "running", "exited", "failed", "killed"] = "created"
    exit_code: int | None = None  # 退出码；< 0 表示被 signal 杀死（|exit_code| 是信号号）


_JOBS_RUNNING: dict[str, _Job] = {}        # job_id → job
# 2026-09-30 修复：进程内锁包住 check+spawn——防双请求同时过 _channel_busy 复跑同一 JOBDIR
# 互毁指纹/队列；仅限**进程内**（uvicorn 多 worker 仍可能并发——已承认为设计上限）
_BUSY_LOCK = threading.Lock()


class CrawlRequest(BaseModel):
    # 默认空列表 = 全部（前端面命令式）；指定 list = 仅跑指定渠道
    channels: list[str] = Field(default_factory=list)
    # 2026-09-30 放宽：incremental 模式完全忽略 limit（用 _seed_count 决定），
    # full 模式才是用户真正控制条数的入口；放宽到 50000 覆盖当前种池总量（7002）+ 余量
    limit: int = Field(default=200, ge=1, le=50000,
                       description="每渠道最大种子数（incremental 模式忽略，full 模式生效）")
    max_pages: int = Field(default=3, ge=1, le=10)
    db_path: str = "data/leads.db"
    # 2026-09-30 智能爬虫：新增 'smart' 模式——自动按 ROI 选最优 channel（绕过冷却）；
    # incremental（默认）= 复用稳定 JOBDIR，Scrapy dupefilter 跳过已爬，
    #                  网络层只抓新种子，最经济（适合日常增量积累）。
    # full = 全新 JOBDIR，每次都从头开始（适合重检旧站点 / 字段 diff / 试新版本）。
    # smart = 智能模式：自动按 ROI 评分选最佳 channel（front-end 用户点一下就跑最优渠道）。
    mode: Literal["incremental", "full", "smart"] = "incremental"


class JobInfo(BaseModel):
    job_id: str
    channel: str
    pid: int
    started_at: float
    status: str
    exit_code: int | None = None
    log: str | None = None


router = APIRouter()


def _pick_seed(channel: str) -> str | None:
    """按优先级选种子文件——新格式（如带 developer_name 的 play-new）优先。"""
    if channel == "play":
        candidates = ["data/seeds-play-new.txt", "data/seeds-play.txt"]
    elif channel == "myshopify":
        candidates = ["data/seeds-myshopify.txt"]
    elif channel == "osm":
        candidates = ["data/seeds-osm.txt"]
    elif channel == "itunes":
        candidates = ["data/seeds-itunes.txt"]
    elif channel == "sample":
        # sample = 手工/测试清单：支持 API 触发（QA 离线端到端走这条路）
        candidates = ["data/seeds-sample.txt"]
    else:
        return None
    for c in candidates:
        if (_CWD / c).exists():
            return c
    return None


def _spawn(channel: str, seed_file: str, limit: int, max_pages: int,
           db_path: str, mode: str = "incremental",
           throttle: '_sc.ThrottleTuning | None' = None,
           chained: bool = False) -> _Job:
    """spawn 独立 subprocess 跑一个 channel——不阻塞 API 响应。

    JOBDIR 选择：
    - incremental：复用 data/.job/_inc/<channel>/ 稳定目录，Scrapy dupefilter 跳过已爬
    - full        ：data/.job/_full/<channel>-<timestamp>/ 全新目录，每次从头爬

    2026-09-30 智能爬虫（L3）：根据 ROI 动态设 AUTOTHROTTLE_TARGET_CONCURRENCY 与
    DOWNLOAD_DELAY——高命中快爬、低命中慢爬避免浪费预算。Throttle 参数通过环境变量
    传给子进程（cli.py 读取）。
    """
    if mode == "incremental":
        jobdir = _JOBS / "_inc" / channel
        jobdir.mkdir(parents=True, exist_ok=True)
        # 增量日志追加（2026-09-28 修复：旧 "w" 模式会把仍在跑的上一轮日志清空）
        log_file = _LOGS / f"_inc-{channel}.log"
        # job_id 复用同一稳定 ID（并发同 channel 已被 _channel_busy 409 拦截）
        job_id = f"inc-{channel}"
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        jobdir = _JOBS / "_full" / f"{channel}-{stamp}"
        jobdir.mkdir(parents=True, exist_ok=True)
        log_file = _LOGS / f"full-{channel}-{stamp}.log"
        job_id = f"full-{channel}-{stamp}"

    log_handle = open(log_file, "a", encoding="utf-8")
    cmd = [
        sys.executable, "-m", "app", "crawl",
        "--seed-file", seed_file,
        "--channel", channel,
        "--limit", str(limit),
        "--max-pages", str(max_pages),
        "--db", db_path,
        "--jobdir", str(jobdir),
    ]
    env = os.environ.copy()
    if throttle is not None:
        # 把 throttle 参数塞进环境变量给 cli.py 读取（避免改 cli.py 参数签名）
        env["YOUZI_AUTOTHROTTLE_TARGET_CONCURRENCY"] = str(throttle.target_concurrency)
        env["YOUZI_DOWNLOAD_DELAY"] = str(throttle.download_delay)
    proc = subprocess.Popen(cmd, cwd=str(_CWD), stdout=log_handle,
                            stderr=subprocess.STDOUT, start_new_session=True,
                            env=env)
    log_handle.close()
    job = _Job(job_id=job_id, channel=channel, pid=proc.pid,
               started_at=time.time(), proc=proc,
               status="running")
    _JOBS_RUNNING[job_id] = job
    _record_job(job, kind="crawl", chained=chained, log_file=log_file)
    return job


def _seed_count(path: Path) -> int:
    """种子文件有效行数（URL each line，# 注释与空行不算）。"""
    with open(path, encoding="utf-8") as fh:
        return sum(1 for ln in fh
                   if ln.strip() and not ln.lstrip().startswith("#"))


def _channel_busy(channel: str) -> bool:
    """该渠道是否仍有活着的 job（先收割已退出进程的真实状态再判）。

    2026-10-01 修复（重启窗口双爬）：内存判不忙后兜底查 job 表——API 重启后
    _JOBS_RUNNING 清空，但孤儿爬取子进程（start_new_session 脱离进程组）仍活
    且 DB 行仍 running。不兜底会再 spawn 同 _inc JOBDIR，双进程互毁指纹/队列
    （正是 _BUSY_LOCK 注释要防的事故）。pid 已死的行不挡（留给 reaper 收割）。
    """
    import os as _os
    for j in list(_JOBS_RUNNING.values()):   # 拷贝：reaper 并发插入防 dict 变更异常
        if j.channel != channel:
            continue
        if j.proc is not None:
            rc = j.proc.poll()
            if rc is not None:
                _finalize(j, rc)   # POSIX: rc<0 = 信号杀死；0 正常；>0 崩溃
                continue
        if j.status in ("created", "running"):
            return True
    try:
        conn = _db.connect(str(_CWD / "data" / "leads.db"))
        try:
            rows = conn.execute(
                "SELECT job_id, pid FROM job WHERE status='running' AND channel=?",
                (channel,)).fetchall()
        finally:
            conn.close()
    except Exception:
        return False
    for r in rows:
        if r["job_id"] in _JOBS_RUNNING:
            continue               # 有内存句柄的已由上面处理过
        try:
            _os.kill(r["pid"], 0)
            return True            # 活孤儿进程仍占着该渠道
        except (ProcessLookupError, TypeError):
            continue               # pid 已死 → 不挡，_reap_lost_jobs 会收割
        except PermissionError:
            return True
    return False


def _safe_db_path(p: str) -> str:
    """db_path 只允许项目内相对路径（2026-09-30 强化 symlink 防护）。

    无鉴权端点 + 任意路径 → SQLite 在 cwd 建库可能写穿：
    - `data/../../etc/x.db` 路径穿越（已挡）
    - `data/leads.db -> /etc/passwd` 符号链接攻击（旧版 `..` 检查不挡——symlink 不在 parts 里）
    修：先与 _CWD 拼接（db_path 在 crawl_api 语义里就是相对 _CWD），resolve 后 must_relative_to(_CWD)
    """
    path = Path(p)
    if path.is_absolute() or ".." in path.parts:
        raise HTTPException(400, f"db_path 必须是项目内相对路径: {p!r}")
    try:
        # db_path 是相对路径，相对 _CWD 解析（crawl_api 路径语义）
        joined = _CWD / path
        resolved = joined.resolve(strict=False)
        if not resolved.is_relative_to(_CWD.resolve()):
            raise HTTPException(400, f"db_path 必须落在项目内: {p!r}")
    except (OSError, ValueError):
        raise HTTPException(400, f"db_path 非法: {p!r}")
    return p


class SeedsRequest(BaseModel):
    """挖新人群（2026-09-30 简化）：选业务场景，后端自动映射国家/类目/渠道。

    销售不填 ISO 国家码、不懂英文类目名——按业务场景一键。
    一个场景可同时触发 play + osm（各自独立 JOBDIR）。
    """
    scene: str = Field(..., description="场景 ID（sea_smb/latam_ecom/mena_biz/africa_new/id_food）")


def _play_proxy() -> str | None:
    """play 渠道代理解析（跨机器部署，2026-09-30）：
    BSP_PROXY 显式指定（空串 = 强制直连）> 本机 127.0.0.1:7890 可达则用 >
    直连。海外 VPS 部署时没有 clash，自动直连——不再硬绑本机代理。"""
    explicit = os.environ.get("BSP_PROXY")
    if explicit is not None:
        return explicit or None
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 7890), timeout=0.3):
            return "http://127.0.0.1:7890"
    except OSError:
        return None


def _spawn_seed(channel: str, countries: str, categories: str,
                limit: int, out_file: str) -> _Job:
    """后台生成种子并 --append 去重追加（Overpass/play node 慢，不阻塞 API）。

    job 进 _JOBS_RUNNING（跑批页可见进度）；同渠道互斥复用 _channel_busy——
    种子任务写种子文件期间不允许同渠道再起爬取/种子任务（文件完整性）。
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_file = _LOGS / f"seed-{channel}-{stamp}.log"
    job_id = f"seed-{channel}-{stamp}"
    cmd = [sys.executable, "-m", "app", "seed", channel,
           "--limit", str(limit), "--out", out_file, "--append"]
    if countries:
        cmd += ["--country", countries]
    if channel in ("play", "itunes"):
        cmd += ["--category", categories]   # 两个 App 渠道都按类目分榜取种子
    log_handle = open(log_file, "a", encoding="utf-8")
    # CN 出口直连 Google Play 超时（docs 3.2）：play 的 node 脚本需要
    # NODE_USE_ENV_PROXY=1 + 代理才走 proxy。代理来源 _play_proxy()——
    # 有就用（含本机 clash 探测），没有（海外 VPS）直连
    env = None
    if channel == "play" and (proxy := _play_proxy()):
        env = {**os.environ, "NODE_USE_ENV_PROXY": "1",
               "HTTPS_PROXY": proxy, "HTTP_PROXY": proxy}
    proc = subprocess.Popen(cmd, cwd=str(_CWD), stdout=log_handle,
                            stderr=subprocess.STDOUT, start_new_session=True,
                            env=env)
    log_handle.close()
    job = _Job(job_id=job_id, channel=channel, pid=proc.pid,
               started_at=time.time(), proc=proc, log_file=log_file,
               status="running")
    _JOBS_RUNNING[job_id] = job
    _record_job(job, kind="seed", chained=False, log_file=log_file)
    return job


# ============================================================
# 2026-09-30 智能爬取加固：job 落库 + 后台收割线程 + seed→crawl 自动接续
# （grilling 共识 Q5/Q9/Q10/Q11：DB 单一事实源，关掉浏览器接续不断）
# ============================================================
def _record_job(job: _Job, *, kind: str, chained: bool, log_file: Path) -> None:
    """spawn 即落 job 表（失败不阻塞主流程——内存 dict 仍可兜底收割）。"""
    try:
        conn = _db.connect(str(_CWD / "data" / "leads.db"))
        try:
            _db.insert_job(conn, job_id=job.job_id, kind=kind, channel=job.channel,
                           pid=job.pid, started_at=job.started_at,
                           chained=chained, log=str(log_file))
        finally:
            conn.close()
    except Exception:
        pass


def _finalize(job: _Job, rc: int) -> None:
    """job 退出 → 内存 + DB 同步终态（rc<0 = 信号杀死，区别于崩溃）。"""
    job.exit_code = rc
    job.status = "exited" if rc == 0 else ("killed" if rc < 0 else "failed")
    job.proc = None
    try:
        conn = _db.connect(str(_CWD / "data" / "leads.db"))
        try:
            _db.finish_job(conn, job.job_id, job.status, rc)
        finally:
            conn.close()
    except Exception:
        pass


def _unseen_estimate(channel: str) -> int:
    """渠道剩余未爬种子估计 = 池内 host 不在 domain 表的数量。

    2026-09-30 三版定稿：①requests.seen 行数（新 Scrapy 是 pickle 二进制，行数恒 ≈1
    → 全判"未爬"）；②dupefilter 指纹数（同上不可解析）。业务真值：每爬一个实体
    upsert_domain 必写行（error 实体也写——爬失败的站重爬无益，计入 seen 语义正确）。
    """
    from app.normalize import entity_key as _ek
    hosts: set[str] = set()
    for cand in _seed_candidates(channel):
        try:
            with open(cand, encoding="utf-8") as fh:
                for line in fh:
                    raw = line.strip()
                    if not raw or raw.startswith("#"):
                        continue
                    u = raw.split("\t", 1)[0]
                    host = urlsplit(u if "://" in u else f"https://{u}").hostname
                    if host:
                        hosts.add(_ek(host.lower()))
        except OSError:
            continue
    if not hosts:
        return 0
    try:
        conn = _db.connect(str(_CWD / "data" / "leads.db"))
        try:
            ph = ",".join("?" * len(hosts))
            seen = conn.execute(
                f"SELECT COUNT(DISTINCT entity_key) FROM domain "
                f"WHERE entity_key IN ({ph})", tuple(hosts)).fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return 0
    return len(hosts) - seen


def _reap_pass() -> set[str]:
    """poll 全部内存 job，收割已退出的。

    返回「本轮收割为成功（exit 0）的 seed job 渠道集合」——自动接续的触发信号。
    """
    done_seeds: set[str] = set()
    for job in list(_JOBS_RUNNING.values()):
        if job.proc is None:
            continue
        rc = job.proc.poll()
        if rc is None:
            continue
        _finalize(job, rc)
        if job.job_id.startswith("seed-") and rc == 0:
            done_seeds.add(job.channel)
    return done_seeds


def _auto_chain(channel: str) -> None:
    """seed 落地 → 自动接续该渠道增量爬（Q1 体感保证的核心链路）。

    只爬种子刚落地的渠道（Q10 最小扰动）；并发安全复用 _BUSY_LOCK。
    """
    with _BUSY_LOCK:
        if _channel_busy(channel):
            return
        cands = _seed_candidates(channel)
        if not cands:
            return
        # ponytail: 接续直喂最大种子文件不走 L1 排序——接续是全量预算，排序无增益
        seed_path = max(cands, key=lambda p: p.stat().st_size)
        limit = sum(_seed_count(c) for c in cands)
        ch_stats = None
        try:
            conn = _db.connect(str(_CWD / "data" / "leads.db"))
            try:
                ch_stats = _db.get_crawl_stats(conn, channel)
            finally:
                conn.close()
        except Exception:
            pass
        _spawn(channel, str(seed_path), limit, 3, "data/leads.db", "incremental",
               throttle=_sc.compute_throttle(channel, ch_stats), chained=True)


def _reap_lost_jobs() -> set[str]:
    """收割跨重启丢失句柄的 job：DB running 但不在内存 dict（上个进程 spawn 的）。

    pid 已死 → 直接 finalize DB 行 + 返回成功 seed 的渠道（触发接续）；
    pid 活着 → 保留（下轮再查）。# ponytail: 轮询 pid 而非句柄，粗但可靠
    """
    import os as _os
    done_seeds: set[str] = set()
    try:
        conn = _db.connect(str(_CWD / "data" / "leads.db"))
        try:
            rows = conn.execute(
                "SELECT rowid, job_id, kind, channel, pid FROM job "
                "WHERE status='running'").fetchall()
        finally:
            conn.close()
    except Exception:
        return done_seeds
    live_ids = set(_JOBS_RUNNING)
    for r in rows:
        if r["job_id"] in live_ids:
            continue   # 有句柄的走 _reap_pass
        try:
            _os.kill(r["pid"], 0)
            continue    # 还活着
        except (ProcessLookupError, TypeError):
            pass
        except PermissionError:
            continue
        status = "exited"   # 无退出码可查——按正常结束处理（保守：exit 0）
        try:
            conn = _db.connect(str(_CWD / "data" / "leads.db"))
            try:
                _db.finish_job(conn, r["job_id"], status, 0)
            finally:
                conn.close()
        except Exception:
            pass
        if r["kind"] == "seed":
            done_seeds.add(r["channel"])
    return done_seeds


def _reaper_loop() -> None:
    """收割线程主体：5s 一轮，永不退出（异常吞掉下轮再来）。"""
    while True:
        time.sleep(5)
        try:
            done = _reap_pass() | _reap_lost_jobs()
            for ch in done:
                _auto_chain(ch)
        except Exception:
            pass


_REAPER: threading.Thread | None = None


def start_reaper() -> None:
    """uvicorn lifespan 调用：先收割 DB 孤儿行（上次重启遗留），再起 daemon 线程。"""
    global _REAPER
    if _REAPER is not None and _REAPER.is_alive():
        return
    try:
        conn = _db.connect(str(_CWD / "data" / "leads.db"))
        try:
            _db.reap_orphan_jobs(conn)
        finally:
            conn.close()
    except Exception:
        pass
    _REAPER = threading.Thread(target=_reaper_loop, daemon=True, name="job-reaper")
    _REAPER.start()


@router.get("/api/scenes")
def get_scenes():
    """业务场景列表（前端挖新人群弹窗渲染卡片用）。"""
    from app.seeds_scenes import SCENES
    return [{"id": s.id, "label": s.label, "pitch": s.pitch,
             "channels": [p.channel for p in s.plans]}
            for s in SCENES]


def _seed_candidates(channel: str) -> list[Path]:
    """返回渠道的所有候选种子文件路径（按优先级排；多个都计入种子池行数）。"""
    if channel == "play":
        names = ["data/seeds-play-new.txt", "data/seeds-play.txt"]
    elif channel == "myshopify":
        names = ["data/seeds-myshopify.txt"]
    elif channel == "osm":
        names = ["data/seeds-osm.txt"]
    elif channel == "itunes":
        names = ["data/seeds-itunes.txt"]
    elif channel == "sample":
        names = ["data/seeds-sample.txt"]   # 手工/测试清单：QA 离线端到端入口
    else:
        return []
    return [p for n in names if (_CWD / n).exists() for p in [_CWD / n]]


@router.get("/api/seeds")
def get_seed_pools():
    """各渠道当前种子池行数（候选文件求和）——前端管道状态条用。

    ponytail：原实现只取 _pick_seed 的首个候选，tranco 兜底永远显示 5 行（忽略
    seeds-tranco-batch2.txt 的 5874 行）。改为所有候选文件求和——"种子池"反映真实总量。
    """
    pools: dict[str, int] = {}
    details: dict[str, dict[str, int]] = {}
    for ch in ALL_CHANNELS:
        cands = _seed_candidates(ch)
        per_file = {c.name: _seed_count(c) for c in cands}
        pools[ch] = sum(per_file.values())
        if per_file:
            details[ch] = per_file
    return {"pools": pools, "details": details}


@router.post("/api/seeds")
def post_seeds(req: SeedsRequest):
    """按业务场景挖新人群：自动展开到 1–2 个渠道子任务并行 spawn。

    ponytail: 一个场景 → 多个 ChannelPlan → 每个独立 spawn → 每个独立 JOBDIR。
    失败隔离：play 失败不影响 osm；前端可看每个子 job 进度。
    """
    from app.seeds_scenes import get_scene

    scene = get_scene(req.scene)
    if scene is None:
        raise HTTPException(400, f"未知场景: {req.scene!r}（GET /api/scenes 看可用列表）")

    # 进程内 TOCTOU 锁：check_busy + spawn 必须原子——否则双请求同时过检查并发 spawn
    # 同 JOBDIR 互毁指纹/队列（2026-09-30 修复）
    with _BUSY_LOCK:
        # 同场景内同渠道互斥（文件读写冲突）
        busy = [p.channel for p in scene.plans if _channel_busy(p.channel)]
        if busy:
            raise HTTPException(
                409, f"场景 {scene.label} 涉及的渠道 {busy} 已有运行中的种子任务（文件读写互斥），稍后再试")

        spawned: list[JobInfo] = []
        targets: list[str] = []
        for plan in scene.plans:
            target = _pick_seed(plan.channel) or f"data/seeds-{plan.channel}.txt"
            # 2026-09-30 修复：play 的 --num 是 per-(country,category) 组合数（scripts/play-seeds.mjs
            # 内每个 combo 拿 num 个），传入 per_country 即可；之前误乘国家数导致 5–15× 过度查询
            # Google Play 触发限流概率大增
            job = _spawn_seed(plan.channel, plan.countries, plan.categories,
                              plan.per_country, target)
            spawned.append(JobInfo(
                job_id=job.job_id, channel=job.channel, pid=job.pid,
                started_at=job.started_at, status=job.status,
            ))
            targets.append(target)

    return {
        "scene": {"id": scene.id, "label": scene.label, "pitch": scene.pitch},
        "jobs": [j.model_dump() for j in spawned],
        "targets": targets,
        "hint": "后台约 1–3 分钟；完成后点「智能爬取」吃新种子",
    }


# 智能爬取黑盒状态：场景轮换指针（存 data/ 内——随数据目录整体迁移到新机器）
_SMART_ROTATION = _CWD / "data" / ".smart-rotation"


# smart「必重访」最小间隔：同渠道 6h 内不重复 full 重访（防每次点击反复重访
# 同一批 URL——attempts/errors 膨胀人为压低 hit_rate，ROI 信号自毁）
RECRAWL_MIN_INTERVAL_S = 6 * 3600


def _full_recrawl_due(channel: str) -> bool:
    """距上次 full 重访 ≥ RECRAWL_MIN_INTERVAL_S（job 表真值；无记录=到期）。"""
    try:
        conn = _db.connect(str(_CWD / "data" / "leads.db"))
        try:
            row = conn.execute(
                "SELECT MAX(finished_at) AS t FROM job "
                "WHERE kind='crawl' AND channel=? AND job_id LIKE 'full-%'",
                (channel,)).fetchone()
        finally:
            conn.close()
    except Exception:
        return True
    if not row or row["t"] is None:
        return True
    return (time.time() - float(row["t"])) >= RECRAWL_MIN_INTERVAL_S


def _write_ranked_seed(ch: str, cands: list[Path], limit: int) -> str:
    """读全部候选种子文件 → L1 历史产出排序 → 覆盖写 .seed-ranked-<ch>.txt。

    固定文件名：同渠道并发已被 409 守卫挡住，覆盖写不互踩且不堆积临时文件。
    2026-10-01：smart「必重访」复用同一排序——旧实现重访吃原始种子文件前 N 行，
    每次都是同一批 URL，新挂 wa.me 的高产域永远轮不到。
    """
    all_urls: list[str] = []
    for cand in cands:
        with open(cand, encoding="utf-8") as fh:
            for line in fh:
                raw = line.strip()
                if not raw or raw.startswith("#"):
                    continue
                url_part = raw.split("\t", 1)[0]
                u = normalize_url(url_part if "://" in url_part
                                  else f"https://{url_part}")
                if u:
                    all_urls.append(u)
    rank_conn = _db.connect(str(_CWD / "data" / "leads.db"))
    try:
        ranked = _sc.rank_seeds_by_yield(rank_conn, all_urls)
    finally:
        rank_conn.close()
    out = _CWD / "data" / f".seed-ranked-{ch}.txt"
    with open(out, "w", encoding="utf-8") as fh:
        fh.writelines(u + "\n" for u in ranked[:limit])
    return str(out)


def _pick_recrawl_target(channels: list[str]) -> str | None:
    """2026-10-01：smart 模式「必重访」选 channel。

    选候选渠道里 attempts 最大的（已知实体最多 → 重访找到新挂 wa.me 的概率
    最高）；attempts < 50（渠道刚起步）返回 None。
    候选 = 本次 spawn 的渠道；0-spawn 且 0-seed 时 = free 渠道（全冷却窗口
    兜底——主循环把冷却渠道 skip 光时，full 重访是「点击→必有任务」的最后出口）。
    """
    if not channels:
        return None
    from app import db as _db
    conn = _db.connect(str(_CWD / "data" / "leads.db"))
    try:
        best_ch = None
        best_attempts = -1
        for ch in channels:
            stats = _db.get_crawl_stats(conn, ch) or {}
            attempts = stats.get("attempts", 0) or 0
            if attempts > best_attempts:
                best_attempts = attempts
                best_ch = ch
        return best_ch if best_attempts >= 50 else None
    finally:
        conn.close()


# 补种退避 TTL：streak≥2 且最近一次失败在 30min 内才剔除——过 TTL 自动放行
# 重试（旧逻辑永久静默剔除：Overpass 间歇故障后渠道永不自愈，也无 skipped 解释）
_SEED_BACKOFF_TTL_S = 30 * 60


def _seed_backing_off(channel: str, streaks: dict[str, int]) -> bool:
    """该渠道补种是否处于失败退避中（streak 门槛 + 时间窗双条件）。"""
    if streaks.get(channel, 0) < 2:
        return False
    try:
        conn = _db.connect(str(_CWD / "data" / "leads.db"))
        try:
            row = conn.execute(
                "SELECT MAX(finished_at) AS t FROM job "
                "WHERE kind='seed' AND channel=? AND status='failed'",
                (channel,)).fetchone()
        finally:
            conn.close()
    except Exception:
        return False
    if not row or row["t"] is None:
        return False
    return (time.time() - float(row["t"])) < _SEED_BACKOFF_TTL_S


def _smart_expand_scene(seed_channels: set[str]) -> list[JobInfo]:
    """smart 模式自带的「挖新人群」：给**池尽**渠道补种。

    2026-10-01 修复：
    - 失败 streak ≥ 2 的渠道跳过——避免 Overpass 挂掉时每次点击都 spawn
      一个失败的 OSM seed（之前完全没退避，前端"一直爬取中"+线索零增长）
    - 兜底：剩余 starved 渠道（如 myshopify——不在任何 SCENES plans 里）
      走直接 spawn，按渠道兜底参数（myshopify 走 CDX 无 country/category）

    从轮换指针起找第一个覆盖目标渠道的场景（定向——不浪费轮换位）；
    指针推进到被选场景。防刷：已有 seed job 在跑就跳过本轮。
    # ponytail: 轮换无间隔上限，循环一周后重展开同场景（URL 去重靠 append_merge）
    """
    if any(j.job_id.startswith("seed-") and j.status in ("created", "running")
           for j in list(_JOBS_RUNNING.values())):
        return []
    # 失败 streak 过滤（2026-10-01 加 TTL）：连续失败 ≥2 且最近一次失败在
    # 30min 内才剔除——过 TTL 放行重试，渠道不再被永久静默剔除
    try:
        conn = _db.connect(str(_CWD / "data" / "leads.db"))
        try:
            fail_streaks = _db.seed_fail_streaks(conn, window=2)
        finally:
            conn.close()
    except Exception:
        fail_streaks = {}
    seed_channels = {c for c in seed_channels
                     if not _seed_backing_off(c, fail_streaks)}
    if not seed_channels:
        return []
    from app.seeds_scenes import SCENES
    base = 0
    if _SMART_ROTATION.exists():
        try:
            base = int(_SMART_ROTATION.read_text(encoding="utf-8").strip())
        except ValueError:
            base = 0
    scene = None
    for off in range(1, len(SCENES) + 1):
        cand = SCENES[(base + off) % len(SCENES)]
        if any(p.channel in seed_channels and not _channel_busy(p.channel)
               for p in cand.plans):
            scene = cand
            _SMART_ROTATION.parent.mkdir(parents=True, exist_ok=True)
            _SMART_ROTATION.write_text(str((base + off) % len(SCENES)), encoding="utf-8")
            break
    spawned: list[JobInfo] = []
    spawned_channels: set[str] = set()
    if scene is not None:
        for plan in scene.plans:
            if plan.channel not in seed_channels or _channel_busy(plan.channel):
                continue
            target = _pick_seed(plan.channel) or f"data/seeds-{plan.channel}.txt"
            job = _spawn_seed(plan.channel, plan.countries, plan.categories,
                              plan.per_country, target)
            spawned.append(JobInfo(job_id=job.job_id, channel=job.channel,
                                   pid=job.pid, started_at=job.started_at,
                                   status=job.status))
            spawned_channels.add(plan.channel)
    # 兜底：剩余 starved 渠道——myshopify 等不在任何 SCENES plans 里
    # 之前这些渠道 starve 永远等不到补种（智能爬取对它"已无能为力"）
    remaining = seed_channels - spawned_channels
    for ch in remaining:
        if _channel_busy(ch):
            continue
        target = _pick_seed(ch) or f"data/seeds-{ch}.txt"
        # 兜底参数：myshopify 无 country/category（CDX 通配查 *.myshopify.com）；
        # osm 默认 SEA 五国；play 默认 BUSINESS 多国
        if ch == "myshopify":
            countries, categories, limit = "", "", 500
        elif ch == "osm":
            countries, categories, limit = "id,th,vn,ph,my", "", 200
        elif ch == "play":
            countries, categories, limit = "id,br,mx", "BUSINESS,SHOPPING", 150
        elif ch == "itunes":
            countries, categories, limit = "id,br,mx", "BUSINESS,SHOPPING", 200
        else:
            countries, categories, limit = "", "", 500
        job = _spawn_seed(ch, countries, categories, limit, target)
        spawned.append(JobInfo(job_id=job.job_id, channel=ch, pid=job.pid,
                               started_at=job.started_at, status=job.status))
        spawned_channels.add(ch)
    return spawned


@router.post("/api/crawl")
def post_crawl(req: CrawlRequest):
    """触发爬取。channels=空/未传 = 全部可爬渠道；非空 = 仅指定渠道。

    mode:
    - incremental（默认）：复用稳定 JOBDIR，dupefilter 跳过已爬；适合日常增量
    - full：全新 JOBDIR，每次从头爬；适合重检 / 字段 diff
    """
    # 2026-09-30 审计修复：请求内渠道去重——["play","play"] 会在同一次锁内对同一
    # JOBDIR 双 spawn（busy 检查只在循环前做一次），互毁指纹/队列
    channels = list(dict.fromkeys(
        req.channels or [c for c in ALL_CHANNELS if c != "sample"]))
    seeded: list[JobInfo] = []   # smart 模式：本次触发的补种任务（响应回传数量）
    free: list[str] = []         # smart 预决策的空闲渠道（下方 recrawl 兜底复用）
    starved: set[str] = set()    # smart 预决策的池尽渠道

    # 校验
    invalid = [c for c in channels if c not in ALL_CHANNELS]
    if invalid:
        raise HTTPException(400, f"未知渠道: {invalid}; 可选: {list(ALL_CHANNELS)}")
    db_path = _safe_db_path(req.db_path)

    # 2026-09-30 智能模式（销售一键黑盒，Q1「点击→最终必递增」闭环）：
    #   ① 池尽判定（unseen 估计 < 100）：池尽渠道去补种（爬了也 0），种子落地后
    #      由收割线程自动接续爬——不占本次点击
    #   ② 池未尽的空闲渠道按 ROI 排序全爬（冷却跳过；低 ROI 由 throttle 保守）
    if req.mode == "smart":
        free = [c for c in channels if not _channel_busy(c)]
        if not free:
            # Q14 幂等合并：点正在跑的批次不是错误——200 + 中性提示（前端不特判 409）
            return {
                "mode": "smart", "spawned": [], "total": 0,
                "skipped": [{"channel": c, "reason": "已有任务在跑（爬取或补种中）"}
                            for c in channels],
                "hint": "本批进行中——完成后自动补种接续，无需重复点击",
            }
        starved = {c for c in free if _unseen_estimate(c) < 100}
        crawlable = [c for c in free if c not in starved]
        seeded = _smart_expand_scene(seed_channels=starved)
        if crawlable:
            conn_ro = _db.connect(str(_CWD / "data" / "leads.db"))
            try:
                stats_map = {s["channel"]: s for s in _db.get_all_crawl_stats(conn_ro)}
                ranked = _sc.rank_channels(crawlable, stats_map)
            finally:
                conn_ro.close()
            ok = [s.channel for s in ranked if s.recommendation != "backoff"]
            channels = ok or ([ranked[0].channel] if ranked else [])
        else:
            channels = []
        if not channels and not seeded:
            # 渠道全在冷却且无种可补——最高分兜底爬（给冷却恢复机会）
            conn_ro = _db.connect(str(_CWD / "data" / "leads.db"))
            try:
                stats_map = {s["channel"]: s for s in _db.get_all_crawl_stats(conn_ro)}
                ranked = _sc.rank_channels(free, stats_map)
            finally:
                conn_ro.close()
            channels = [ranked[0].channel] if ranked else []

    # 并发守卫（2026-09-30 强化）：进程内锁包住 check+spawn——双请求同时过
    # _channel_busy 会并发 spawn 同 JOBDIR，Scrapy 指纹/断点队列互相覆盖
    with _BUSY_LOCK:
        busy = [ch for ch in set(channels) if _channel_busy(ch)]
        if busy:
            raise HTTPException(
                409, f"渠道 {sorted(busy)} 已有运行中的 job（共用 JOBDIR 不允许并发），"
                     f"先等它结束或查 /api/crawl/status")

        spawned: list[JobInfo] = []
        skipped: list[dict] = []
        # 2026-10-01：补种被暂缓不再静默——streak 退避/任务忙时写明原因
        if req.mode == "smart" and starved and not seeded:
            skipped.append({"channel": ",".join(sorted(starved)),
                            "reason": "补种暂缓（已有补种在跑或渠道失败退避中，30 分钟自动重试）"})
        targets: list[str] = []
        for ch in channels:
            # 2026-09-30 修复：incremental 模式汇总所有候选种子文件行数（与 /api/seeds 一致），
            # 并选最大的文件喂 Scrapy——避免 _pick_seed 只取首个候选（tranco 兜底永远 5 行
            # 而 batch2 5874 行从未被用，导致增量 tranco 每次只爬 5 URL）。
            candidates = _seed_candidates(ch)
            if not candidates:
                if req.mode == "smart":
                    # L2 兜底：池子空 → 后台自动补该渠道种子（走 _spawn_seed 进 busy 守卫）
                    _spawn_seed(ch, "", "", 500, f"data/seeds-{ch}.txt")
                    skipped.append({"channel": ch, "reason": "无种子文件（已在后台补充，稍后再点）"})
                else:
                    skipped.append({"channel": ch, "reason": f"无种子文件（需先跑 seed {ch}）"})
                continue
            # 冷却中跳过（其他模式也尊重冷却——admin 用 clear-backoff 解除）
            ch_conn = _db.connect(str(_CWD / "data" / "leads.db"))
            try:
                ch_stats = _db.get_crawl_stats(ch_conn, ch)
            finally:
                ch_conn.close()
            # 2026-09-30 审计修复：backoff_until 经 SQLite datetime() 落库为 naive
            # "YYYY-MM-DD HH:MM:SS"，旧 fromisoformat+aware now 比较抛 TypeError 被吞
            # → 冷却从不生效（死渠道照爬浪费预算）。统一走 parse_backoff_until aware 化。
            bt = _sc.parse_backoff_until(ch_stats.get("backoff_until")) if ch_stats else None
            if bt and bt > datetime.now(timezone.utc):
                skipped.append({"channel": ch, "reason": "冷却中"})
                continue
            # 选最大文件作 fallback（Scrapy 内部 dupefilter 共享 JOBDIR——重复 URL 跨文件自动跳过）
            seed_path = max(candidates, key=lambda p: p.stat().st_size)
            # 2026-09-30 L1 per-seed scoring：按历史 yield_score 排序，高分优先爬
            # 同 crawl budget 下优先验证「已知有钱」的域。读全部候选文件（增量
            # 模式 limit=全部候选行数之和，已被爬过的交给 dupefilter 跳过）。
            if req.mode in ("incremental", "smart"):
                limit = sum(_seed_count(c) for c in candidates)
            else:
                limit = req.limit
            try:
                seed_file = _write_ranked_seed(ch, candidates, limit)
            except Exception:
                # 排序失败 → fallback 到原文件
                seed_file = str(seed_path)
            target = _pick_seed(ch) or f"data/seeds-{ch}.txt"
            # 2026-09-30 L3 per-channel AutoThrottle：按 ROI 动态设并发/延迟
            # 高命中快爬、低命中慢爬（保守用预算）
            throttle = _sc.compute_throttle(ch, ch_stats)
            job = _spawn(ch, seed_file, limit, req.max_pages, db_path,
                         "incremental" if req.mode == "smart" else req.mode,
                         throttle=throttle)
            spawned.append(JobInfo(
                job_id=job.job_id, channel=ch, pid=job.pid,
                started_at=job.started_at, status=job.status,
            ))
            targets.append(target)

        # 2026-10-01 smart 模式「必重访」：增量模式用稳定 JOBDIR → Scrapy dupefilter
        # 跳过已爬 URL → 重访发现新挂 wa.me 的机会 = 0。强制 mode=full + 新 JOBDIR
        # 重爬一次已知实体，找"上次爬时漏掉 / 之后新加"的 wa.me 链接。
        # 限制 200 URL（够覆盖常见 WA 高产域，避免长跑）。
        if req.mode == "smart":
            # 2026-10-01 P0-2 修复：候选优先取本次 spawn 的渠道；0-spawn 且 0-seed
            #（典型=全冷却窗口被主循环 skip 光）→ 兜底用 free 渠道。full 重访不查
            # 冷却且用独立 JOBDIR——这是「点击→必有任务」闭环的最后出口。
            recrawl_candidates = ([j.channel for j in spawned]
                                  if spawned else ([] if seeded else free))
            recrawl_ch = _pick_recrawl_target(recrawl_candidates)
            # 2026-10-01 时间闸：同渠道 6h 内不重复 full 重访（旧实现每次点击都
            # 重访同一批 200 URL——attempts 膨胀自毁 ROI 信号 + JOBDIR 无限堆积）
            if recrawl_ch is not None and _full_recrawl_due(recrawl_ch):
                rc = recrawl_ch
                rc_cands = _seed_candidates(rc)
                if rc_cands:
                    rc_pool = sum(_seed_count(c) for c in rc_cands)
                    rc_limit = min(200, max(50, rc_pool // 5))
                    rc_conn = _db.connect(str(_CWD / "data" / "leads.db"))
                    try:
                        rc_stats = _db.get_crawl_stats(rc_conn, rc)
                    finally:
                        rc_conn.close()
                    rc_throttle = _sc.compute_throttle(rc, rc_stats)
                    # 2026-10-01：重访也走 L1 排序（旧实现吃原始文件前 N 行，
                    # 每次同一批 URL——新挂 wa.me 的高产域永远轮不到）
                    try:
                        rc_seed_file = _write_ranked_seed(rc, rc_cands, rc_limit)
                    except Exception:
                        rc_seed_file = str(max(rc_cands,
                                               key=lambda p: p.stat().st_size))
                    rc_job = _spawn(rc, rc_seed_file, rc_limit, 2, db_path,
                                    "full", throttle=rc_throttle)
                    spawned.append(JobInfo(
                        job_id=rc_job.job_id, channel=rc, pid=rc_job.pid,
                        started_at=rc_job.started_at, status=rc_job.status,
                    ))
                    targets.append(_pick_seed(rc) or f"data/seeds-{rc}.txt")

    return {
        "mode": req.mode,
        "spawned": [j.model_dump() for j in spawned],
        "skipped": skipped,
        "total": len(spawned),
        # smart 模式附带：本次触发的补种任务数（种子 1–3 分钟落地后自动接续爬）
        "seeded": len(seeded) if req.mode == "smart" else 0,
    }


def _tail(path: Path | None, n: int = 30, max_bytes: int = 4096) -> str | None:
    """F1 fix: 日志尾部不仅限行数，还限总字节数。Scrapy stats dump 含 datetime
    repr（带 brackets/换行），单行 100KB+ 经常出现；2s 轮询 × 8 jobs = 巨大带宽 + 信息泄露。
    2026-09-28 修复：旧 read_text() 每次轮询整读日志——长爬取上百 MB 时每 2s × N job
    重复全量读。现只 seek 读尾部 64KB（行数/字节上限远小于此，绰绰有余）。
    同时剥离绝对路径前缀，避免暴露 /Users/... 之类的服务端文件位置。
    """
    if not path or not path.exists():
        return None
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 65536))
            chunk = fh.read()
        lines = chunk.decode("utf-8", errors="replace").splitlines(keepends=True)[-n:]
        raw = "".join(lines).encode("utf-8", errors="replace")[-max_bytes:]
        text = raw.decode("utf-8", errors="replace")
        # 绝对路径 → 相对路径，避免内部信息泄露
        import re as _re
        text = _re.sub(r"/[\w/.-]+/app/", "app/", text)
        text = _re.sub(r"/(?:Users|home)/\w+/", "~/", text)
        return text
    except OSError:
        return None


@router.get("/api/crawl/status")
def get_crawl_status():
    """job 状态 + 渠道健康（Q5/Q13：job 表单一事实源，API 重启不丢）。

    返回 {jobs: [...], health: {channel: 连续补种失败次数}}。
    health 只报**近 2 小时内**连败 ≥3 的渠道（当前事故语义——旧失败不永久挂
    横幅；补种每 30 分钟自动重试，恢复即消）。
    """
    # 有人看板时顺带收割，降低收割线程 5s 延迟的体感——但收割到的成功 seed
    # 必须同点接续（2026-10-01 实测修复）：前端 2-5s 轮询 status，seed 任务退出
    # 后几乎必然被 HTTP 线程先收割，丢弃 done_seeds 会让 _auto_chain 永不触发
    # （reaper 再看时 proc=None 已跳过）。幂等：与 reaper 线程并发触发时被
    # _channel_busy 挡住。
    for _ch in _reap_pass():
        _auto_chain(_ch)
    try:
        conn = _db.connect(str(_CWD / "data" / "leads.db"))
        try:
            jobs = _db.list_jobs(conn, 100)
            health = _db.seed_fail_streaks(conn, within_seconds=2 * 3600)
        finally:
            conn.close()
    except Exception:
        return {"jobs": [], "health": {}}
    for j in jobs:
        j["log"] = _tail(Path(j["log"]), 30, max_bytes=4096) if j.get("log") else None
    return {"jobs": jobs, "health": health}