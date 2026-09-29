"""crawl_api 加固回归（2026-09-28 B2）：并发 409 守卫 + db_path 收口 + _tail seek 化。

不真正 spawn 子进程——_spawn 全部 monkeypatch 掉（真实 spawn 会跑 Scrapy 爬取）。
"""
from pathlib import Path

import pytest
from fastapi import HTTPException

from youzi_bsp import crawl_api
from youzi_bsp.crawl_api import _safe_db_path, _tail


@pytest.fixture(autouse=True)
def _clean_jobs():
    yield
    crawl_api._JOBS_RUNNING.clear()


def _fake_job(channel="play", status="running"):
    return crawl_api._Job(job_id=f"inc-{channel}", channel=channel, pid=12345,
                          started_at=0.0, proc=None, status=status)


def test_safe_db_path_rejects_escape():
    """无鉴权端点收口：绝对路径 / 含 .. 的路径 400，项目内相对路径放行。"""
    for bad in ("../../etc/x.db", "/abs/path/x.db", "data/../../x.db"):
        with pytest.raises(HTTPException) as e:
            _safe_db_path(bad)
        assert e.value.status_code == 400
    assert _safe_db_path("data/leads.db") == "data/leads.db"


def test_post_crawl_409_on_busy_channel():
    """同 channel 已有 running job → 409（防两进程共用 JOBDIR 互毁指纹/队列）。"""
    crawl_api._JOBS_RUNNING["inc-play"] = _fake_job("play")
    with pytest.raises(HTTPException) as e:
        crawl_api.post_crawl(crawl_api.CrawlRequest(channels=["play"]))
    assert e.value.status_code == 409


def test_post_crawl_409_before_spawn(monkeypatch):
    """409 必须发生在任何 spawn 之前（防半路 spawn 后才拒绝）。"""
    crawl_api._JOBS_RUNNING["inc-play"] = _fake_job("play")
    spawned = []
    monkeypatch.setattr(crawl_api, "_spawn",
                        lambda *a, **k: spawned.append(1) or _fake_job())
    monkeypatch.setattr(crawl_api, "_pick_seed", lambda ch: "data/x.txt")
    with pytest.raises(HTTPException):
        crawl_api.post_crawl(crawl_api.CrawlRequest(channels=["play", "osm"]))
    assert spawned == []


def test_post_crawl_allows_exited_job(monkeypatch):
    """已退出的 job 不挡新批次（增量续跑常态）。"""
    crawl_api._JOBS_RUNNING["inc-play"] = _fake_job("play", status="exited")
    # 仓库里有真实种子文件——必须打掉 _pick_seed/_spawn，防测试真起爬取进程
    monkeypatch.setattr(crawl_api, "_pick_seed", lambda ch: None)
    monkeypatch.setattr(crawl_api, "_spawn", lambda *a, **k: _fake_job())
    out = crawl_api.post_crawl(crawl_api.CrawlRequest(channels=["play"]))
    assert out["total"] == 0          # 无种子 → skipped 而非 409


def test_tail_reads_only_tail(tmp_path):
    """_tail 只读尾部 64KB：200KB 日志含末行也能取到，且输出 ≤ max_bytes。"""
    log = tmp_path / "big.log"
    log.write_text("x" * 200_000 + "\nLAST-LINE\n", encoding="utf-8")
    text = _tail(log, n=5, max_bytes=4096)
    assert text is not None
    assert "LAST-LINE" in text
    assert len(text.encode()) <= 4096
    assert _tail(tmp_path / "nope.log") is None
