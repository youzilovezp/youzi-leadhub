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
        sys.executable, "-m", "youzi_bsp", "crawl",
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
    """db_path 只允许项目内相对路径（2026-09-28 收口：端点无鉴权，任意字符串
    可在服务器任意可写路径建库写文件）。"""
    path = Path(p)
    if path.is_absolute() or ".." in path.parts:
        raise HTTPException(400, f"db_path 必须是项目内相对路径: {p!r}")
    return p


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

    # 并发守卫（2026-09-28 修复）：同 channel 并发 spawn 会共用同一 JOBDIR，
    # Scrapy 指纹/断点队列互相覆盖——先全量检查，避免半路 spawn 后才 409
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
        job = _spawn(ch, seed, req.limit, req.max_pages, db_path, req.mode)
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
        text = _re.sub(r"/[\w/.-]+/youzi_bsp/", "youzi_bsp/", text)
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
    stale = [jid for jid, j in _JOBS_RUNNING.items()
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