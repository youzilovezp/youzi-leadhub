"""crawl_api 加固回归（2026-09-28 B2）：并发 409 守卫 + db_path 收口 + _tail seek 化。

不真正 spawn 子进程——_spawn 全部 monkeypatch 掉（真实 spawn 会跑 Scrapy 爬取）。
"""
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import crawl_api
from app.crawl_api import _safe_db_path, _tail


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


def test_incremental_limit_is_full_seed_file(monkeypatch, tmp_path):
    """2026-09-29 修复：增量模式 limit=种子全量行数（旧实现取前 200 条，
    第 201+ 条种子永远轮不到 → 增量永远 +0）；full 模式仍用用户 limit。"""
    seed = tmp_path / "seeds-x.txt"
    seed.write_text("https://a.com/\n" * 309 + "# comment\n\n", encoding="utf-8")
    monkeypatch.setattr(crawl_api, "_pick_seed", lambda ch: str(seed))
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    calls = {}
    monkeypatch.setattr(crawl_api, "_spawn",
                        lambda ch, s, limit, mp, db, mode:
                            calls.update(channel=ch, limit=limit, mode=mode)
                            or _fake_job(ch))

    crawl_api.post_crawl(crawl_api.CrawlRequest(channels=["play"],
                                                limit=200,
                                                mode="incremental"))
    assert calls["limit"] == 309      # 全量种子交给 dupefilter 跳 seen

    crawl_api.post_crawl(crawl_api.CrawlRequest(channels=["play"],
                                                limit=200, mode="full"))
    assert calls["limit"] == 200      # full 模式照旧按用户 limit 切片


def test_tail_reads_only_tail(tmp_path):
    """_tail 只读尾部 64KB：200KB 日志含末行也能取到，且输出 ≤ max_bytes。"""
    log = tmp_path / "big.log"
    log.write_text("x" * 200_000 + "\nLAST-LINE\n", encoding="utf-8")
    text = _tail(log, n=5, max_bytes=4096)
    assert text is not None
    assert "LAST-LINE" in text
    assert len(text.encode()) <= 4096
    assert _tail(tmp_path / "nope.log") is None


# ============================================================================
# 挖新人群（2026-09-29）：POST /api/seeds + seeds.append_merge
# ============================================================================

def test_append_merge_dedups_by_url(tmp_path):
    """按 URL 去重追加：旧行（含 developerName）保留，仅新 URL 进池。"""
    from app.seeds import append_merge

    target = tmp_path / "seeds-play.txt"
    target.write_text("https://old.com/\t老开发者\nhttps://dup.com/\n", encoding="utf-8")
    src = tmp_path / "gen.txt"
    src.write_text("https://new.com/\t新开发者\nhttps://dup.com/\n"
                   "# 注释\n\nhttps://old.com/\n", encoding="utf-8")
    assert append_merge(target, src) == 1
    lines = target.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "https://old.com/\t老开发者"   # 旧行原样
    assert lines[-1] == "https://new.com/\t新开发者"  # 只追加新 URL


def test_post_seeds_spawns_append_job(monkeypatch, tmp_path):
    """POST /api/seeds → spawn 带 --append 的种子任务并登记进度。"""
    seed = tmp_path / "seeds-play.txt"
    seed.write_text("https://a.com/\n", encoding="utf-8")
    monkeypatch.setattr(crawl_api, "_pick_seed", lambda ch: str(seed))
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    cmd_seen = {}
    monkeypatch.setattr(crawl_api.subprocess, "Popen",
                        lambda cmd, **k: cmd_seen.update(cmd=cmd, cwd=k["cwd"])
                        or type("P", (), {"pid": 42, "poll": lambda self: None})())
    out = crawl_api.post_seeds(crawl_api.SeedsRequest(
        channel="play", countries="br,mx", categories="SHOPPING", limit=500))
    assert "--append" in cmd_seen["cmd"]
    assert "--country" in cmd_seen["cmd"] and "br,mx" in cmd_seen["cmd"]
    assert "--category" in cmd_seen["cmd"] and "SHOPPING" in cmd_seen["cmd"]
    assert out["job"]["status"] == "running"
    # 同渠道互斥：running 种子任务挡住新种子请求（409）
    with pytest.raises(HTTPException) as e:
        crawl_api.post_seeds(crawl_api.SeedsRequest(channel="play"))
    assert e.value.status_code == 409
