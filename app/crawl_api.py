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
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# 路径：API 进程 cwd 是项目根目录（uvicorn 启动方式决定）；爬取需在该 cwd 下
# 跑才能正确解析相对 seed-file / db 路径
_CWD = Path(os.getcwd()).resolve()
_DATA = _CWD / "data"
_LOGS = _CWD / "data" / ".crawl-logs"
_JOBS = _CWD / "data" / ".job"
_LOGS.mkdir(parents=True, exist_ok=True)
_JOBS.mkdir(parents=True, exist_ok=True)

ALL_CHANNELS = ("tranco", "myshopify", "play", "osm", "sample")
# 渠道 → 默认种子文件映射；sample 渠道需用户传 --file 故不在一键列表
SEEDS_DEFAULT: dict[str, str] = {
    "tranco":    "data/seeds-tranco.txt",
    "myshopify": "data/seeds-myshopify.txt",
    "play":      "data/seeds-play.txt",         # 兜底；如有 seeds-play-new.txt 自动优先
    "osm":       "data/seeds-osm.txt",
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
    status: Literal["created", "running", "exited", "failed"] = "created"
    exit_code: int | None = None


_JOBS_RUNNING: dict[str, _Job] = {}        # job_id → job
# 2026-09-30 修复：进程内锁包住 check+spawn——防双请求同时过 _channel_busy 复跑同一 JOBDIR
# 互毁指纹/队列；仅限**进程内**（uvicorn 多 worker 仍可能并发——已承认为设计上限）
_BUSY_LOCK = threading.Lock()


class CrawlRequest(BaseModel):
    # 默认空列表 = 全部（前端面命令式）；指定 list = 仅跑指定渠道
    channels: list[str] = Field(default_factory=list)
    limit: int = Field(default=200, ge=1, le=2000, description="每渠道最大种子数")
    max_pages: int = Field(default=3, ge=1, le=10)
    db_path: str = "data/leads.db"
    # incremental（默认）= 复用稳定 JOBDIR，Scrapy dupefilter 跳过已爬，
    #                  网络层只抓新种子，最经济（适合日常增量积累）。
    # full = 全新 JOBDIR，每次都从头开始（适合重检旧站点 / 字段 diff / 试新版本）。
    mode: Literal["incremental", "full"] = "incremental"


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
    elif channel == "tranco":
        candidates = ["data/seeds-tranco.txt", "data/seeds-tranco-batch2.txt"]
    elif channel == "myshopify":
        candidates = ["data/seeds-myshopify.txt"]
    elif channel == "osm":
        candidates = ["data/seeds-osm.txt"]
    else:
        return None
    for c in candidates:
        if (_CWD / c).exists():
            return c
    return None


def _spawn(channel: str, seed_file: str, limit: int, max_pages: int,
           db_path: str, mode: str = "incremental") -> _Job:
    """spawn 独立 subprocess 跑一个 channel——不阻塞 API 响应。

    JOBDIR 选择：
    - incremental：复用 data/.job/_inc/<channel>/ 稳定目录，Scrapy dupefilter 跳过已爬
    - full        ：data/.job/_full/<channel>-<timestamp>/ 全新目录，每次从头爬
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
    proc = subprocess.Popen(
        cmd, cwd=str(_CWD),
        stdout=log_handle, stderr=subprocess.STDOUT,
        start_new_session=True,         # 独立 session，API 进程退出不影响
    )
    # 父进程立即关 fd（2026-09-28 修复：旧实现每次 spawn 泄一个描述符；
    # 子进程已通过 stdout 继承，父侧只留句柄没用）
    log_handle.close()
    job = _Job(
        job_id=job_id, channel=channel, pid=proc.pid,
        started_at=time.time(), proc=proc, log_file=log_file,
        status="running",
    )
    _JOBS_RUNNING[job_id] = job
    return job


def _seed_count(path: Path) -> int:
    """种子文件有效行数（URL each line，# 注释与空行不算）。"""
    with open(path, encoding="utf-8") as fh:
        return sum(1 for ln in fh
                   if ln.strip() and not ln.lstrip().startswith("#"))


def _channel_busy(channel: str) -> bool:
    """该渠道是否仍有活着的 job（先收割已退出进程的真实状态再判）。"""
    for j in _JOBS_RUNNING.values():
        if j.channel != channel:
            continue
        if j.proc is not None:
            rc = j.proc.poll()
            if rc is not None:
                j.exit_code = rc
                j.status = "exited" if rc == 0 else "failed"
                j.proc = None
                continue
        if j.status in ("created", "running"):
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
    if channel == "play":
        cmd += ["--category", categories]
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
    return job


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
    elif channel == "tranco":
        names = ["data/seeds-tranco.txt", "data/seeds-tranco-batch2.txt"]
    elif channel == "myshopify":
        names = ["data/seeds-myshopify.txt"]
    elif channel == "osm":
        names = ["data/seeds-osm.txt"]
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
        "hint": f"后台约 1–3 分钟；完成后到跑批页确认退出码，再点「增量爬取」吃新种子",
    }


@router.post("/api/crawl")
def post_crawl(req: CrawlRequest):
    """触发爬取。channels=空/未传 = 全部可爬渠道；非空 = 仅指定渠道。

    mode:
    - incremental（默认）：复用稳定 JOBDIR，dupefilter 跳过已爬；适合日常增量
    - full：全新 JOBDIR，每次从头爬；适合重检 / 字段 diff
    """
    channels = req.channels or [c for c in ALL_CHANNELS if c != "sample"]

    # 校验
    invalid = [c for c in channels if c not in ALL_CHANNELS]
    if invalid:
        raise HTTPException(400, f"未知渠道: {invalid}; 可选: {list(ALL_CHANNELS)}")
    db_path = _safe_db_path(req.db_path)

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
        for ch in channels:
            seed = _pick_seed(ch)
            if not seed or not (_CWD / seed).exists():
                skipped.append({"channel": ch, "reason": f"无种子文件（需先跑 seed {ch}）"})
                continue
            # 增量模式不切片种子文件（2026-09-29 修复）：旧实现取前 limit 条，同一
            # 种子文件反复跑永远轮不到第 limit+1 条（实测：seeds 309 条、limit 200，
            # 18:19 增量 filtered 200 / 0.042s 结束 / 永远 +0）。全量交给 dupefilter
            # 跳已爬——指纹查表极廉价，被滤的种子不产生网络请求。
            limit = (_seed_count(_CWD / seed) if req.mode == "incremental"
                     else req.limit)
            job = _spawn(ch, seed, limit, req.max_pages, db_path, req.mode)
            spawned.append(JobInfo(
                job_id=job.job_id, channel=job.channel, pid=job.pid,
                started_at=job.started_at, status=job.status,
            ))

    return {
        "mode": req.mode,
        "spawned": [j.model_dump() for j in spawned],
        "skipped": skipped,
        "total": len(spawned),
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
        text = _re.sub(r"/Users/\w+/", "~/", text)
        return text
    except OSError:
        return None


@router.get("/api/crawl/status")
def get_crawl_status():
    """返回当前/最近 crawl job 状态（含退出码 + 末尾 30 行/4KB 日志）。

    F2 fix: TTL 清理——只保留最近 100 条；已 exited 超过 1 小时的清出 dict。
    """
    import time as _t
    now = _t.time()
    TTL = 3600  # 已退出 job 在 _JOBS_RUNNING 保留 1 小时供前端查看，之后清
    # TTL 清理：已 exited/failed 且 started_at > 1 小时前的全部清出
    # 2026-09-30 修复：迭代前 `list(_JOBS_RUNNING.items())` 快照——并发 pop 触发
    # `RuntimeError: dictionary changed size during iteration` 导致 API 500
    stale = [jid for jid, j in list(_JOBS_RUNNING.items())
             if j.status in ("exited", "failed") and (now - j.started_at) > TTL]
    for jid in stale:
        _JOBS_RUNNING.pop(jid, None)
    # 大小限幅：保留最近 100 条
    if len(_JOBS_RUNNING) > 100:
        for jid in list(_JOBS_RUNNING.keys())[:len(_JOBS_RUNNING) - 100]:
            _JOBS_RUNNING.pop(jid, None)

    out: list[JobInfo] = []
    for job_id, job in list(_JOBS_RUNNING.items()):
        if job.proc is not None:
            rc = job.proc.poll()
            if rc is None:
                job.status = "running"
            else:
                job.exit_code = rc
                job.status = "exited" if rc == 0 else "failed"
                job.proc = None
        out.append(JobInfo(
            job_id=job.job_id, channel=job.channel, pid=job.pid,
            started_at=job.started_at, status=job.status,
            exit_code=job.exit_code,
            log=_tail(job.log_file, 30, max_bytes=4096),
        ))
    return [j.model_dump() for j in out]