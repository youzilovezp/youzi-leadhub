"""crawl_api 加固回归（2026-09-28 B2）：并发 409 守卫 + db_path 收口 + _tail seek 化。

不真正 spawn 子进程——_spawn 全部 monkeypatch 掉（真实 spawn 会跑 Scrapy 爬取）。
"""
from pathlib import Path
from unittest.mock import patch

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
    """POST /api/seeds → 按业务场景 spawn 一个或多个渠道子任务（2026-09-30 简化）。"""
    seed = tmp_path / "seeds-play.txt"
    seed.write_text("https://a.com/\n", encoding="utf-8")
    monkeypatch.setattr(crawl_api, "_pick_seed", lambda ch: str(seed))
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    cmds = []
    monkeypatch.setattr(crawl_api.subprocess, "Popen",
                        lambda cmd, **k: cmds.append(cmd)
                        or type("P", (), {"pid": 42, "poll": lambda self: None})())
    out = crawl_api.post_seeds(crawl_api.SeedsRequest(scene="latam_ecom"))
    # latam_ecom 含 play + osm 两个 plan → 两条 cmd
    assert len(cmds) == 2
    play_cmd = " ".join(next(c for c in cmds if "play" in c))
    osm_cmd = " ".join(next(c for c in cmds if "osm" in c))
    assert "--append" in play_cmd and "--country" in play_cmd
    assert "br,mx" in play_cmd and "SHOPPING" in play_cmd
    assert "--append" in osm_cmd and "--country" in osm_cmd
    # 返回结构：scene + jobs + targets
    assert out["scene"]["id"] == "latam_ecom"
    assert len(out["jobs"]) == 2
    assert all(j["status"] == "running" for j in out["jobs"])
    # 清掉 fake job 避免 id_food 因 osm busy 撞 409
    crawl_api._JOBS_RUNNING.clear()
    # 未知场景 → 400
    with pytest.raises(HTTPException) as e:
        crawl_api.post_seeds(crawl_api.SeedsRequest(scene="nope"))
    assert e.value.status_code == 400
    # 单渠道场景（id_food 只含 osm）→ 一条 cmd
    cmds.clear()
    crawl_api.post_seeds(crawl_api.SeedsRequest(scene="id_food"))
    assert len(cmds) == 1 and "osm" in cmds[0]


def test_get_scenes_returns_business_scenes():
    """GET /api/scenes 返回 5 个业务场景供前端卡片渲染。"""
    from app.seeds_scenes import SCENES
    out = crawl_api.get_scenes()
    assert len(out) == len(SCENES)
    for s, item in zip(SCENES, out):
        assert item["id"] == s.id
        assert item["label"] == s.label
        # 每场景含 1-2 渠道
        assert 1 <= len(item["channels"]) <= 2
        assert set(item["channels"]).issubset({"play", "osm"})


def test_post_seeds_busy_channel_409(monkeypatch, tmp_path):
    """场景内任一渠道已有 running 种子任务 → 409（避免文件读写互踩）。"""
    seed = tmp_path / "seeds-play.txt"
    seed.write_text("https://a.com/\n", encoding="utf-8")
    monkeypatch.setattr(crawl_api, "_pick_seed", lambda ch: str(seed))
    # 模拟 play 渠道已有 running 任务
    fake_job = crawl_api._Job(job_id="seed-play-x", channel="play", pid=1,
                              started_at=0.0, proc=None, status="running")  # type: ignore[arg-type]
    crawl_api._JOBS_RUNNING["seed-play-x"] = fake_job
    try:
        with pytest.raises(HTTPException) as e:
            crawl_api.post_seeds(crawl_api.SeedsRequest(scene="sea_smb"))
        assert e.value.status_code == 409
    finally:
        crawl_api._JOBS_RUNNING.pop("seed-play-x", None)


def test_get_seed_pools_sums_candidates(monkeypatch, tmp_path):
    """GET /api/seeds：tranco 兜底候选（5 行）+ batch2（5874 行）应求和而非只取首个。

    ponytail：原 bug —— _pick_seed 只返回首个候选，tranco 兜底永远显示 5 行。
    修复：返回 pools + details 两个字段，pools 是各渠道所有候选文件求和。
    """
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    (tmp_path / "data").mkdir()
    # 模拟 tranco 渠道：兜底 5 行 + batch2 5874 行
    (tmp_path / "data" / "seeds-tranco.txt").write_text("\n".join(f"https://a{i}.com/" for i in range(5)))
    (tmp_path / "data" / "seeds-tranco-batch2.txt").write_text("\n".join(f"https://b{i}.com/" for i in range(5874)))
    # 模拟 osm 渠道：单文件 626 行
    (tmp_path / "data" / "seeds-osm.txt").write_text("\n".join(f"https://c{i}.com/" for i in range(626)))

    out = crawl_api.get_seed_pools()
    # tranco 应 = 5 + 5874 = 5879
    assert out["pools"]["tranco"] == 5879
    # osm 应 = 626
    assert out["pools"]["osm"] == 626
    # 其他渠道 = 0
    assert out["pools"]["play"] == 0
    # details 含每个文件的明细
    assert out["details"]["tranco"]["seeds-tranco.txt"] == 5
    assert out["details"]["tranco"]["seeds-tranco-batch2.txt"] == 5874
    assert out["details"]["osm"]["seeds-osm.txt"] == 626


def test_post_seeds_play_no_country_multiplier(monkeypatch, tmp_path):
    """2026-09-30 修复：play 渠道 --num 是 per-(country,category) 组合数——
    不该再乘国家数（之前 sea_smb 5×3=15 combo × num=750 = 11,250 请求，过度）。

    修复后：传入 plan.per_country 原值（150），不再 × len(countries)
    """
    seed = tmp_path / "seeds-play.txt"
    seed.write_text("https://a.com/\n", encoding="utf-8")
    monkeypatch.setattr(crawl_api, "_pick_seed", lambda ch: str(seed))
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    cmds = []
    monkeypatch.setattr(crawl_api.subprocess, "Popen",
                        lambda cmd, **k: cmds.append(cmd)
                        or type("P", (), {"pid": 42, "poll": lambda self: None})())
    crawl_api._JOBS_RUNNING.clear()
    # sea_smb: play 5 国, per_country=150
    crawl_api.post_seeds(crawl_api.SeedsRequest(scene="sea_smb"))
    play_cmd = " ".join(next(c for c in cmds if "play" in c))
    # 关键断言：--limit 应是 per_country=150，不是 150 × 5 = 750
    assert "--limit 150" in play_cmd, f"play --limit 错误（应=per_country=150）: {play_cmd}"


def test_post_crawl_toctou_lock(monkeypatch, tmp_path):
    """2026-09-30 修复：_BUSY_LOCK 包住 check+spawn——双请求同时过 _channel_busy
    不会并发 spawn（已有 running 的会被 409 拦截）。
    """
    seed = tmp_path / "seeds-tranco.txt"
    seed.write_text("https://a.com/\n", encoding="utf-8")
    monkeypatch.setattr(crawl_api, "_pick_seed", lambda ch: str(seed))
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    # 模拟 tranco 已有 running job
    fake = crawl_api._Job(job_id="inc-tranco", channel="tranco", pid=1,
                          started_at=0.0, proc=None, status="running")  # type: ignore[arg-type]
    crawl_api._JOBS_RUNNING["inc-tranco"] = fake
    spawned = []
    monkeypatch.setattr(crawl_api, "_spawn",
                        lambda *a, **k: spawned.append(1) or fake)
    try:
        # 第一个请求应 409，不应 spawn
        with pytest.raises(HTTPException) as e:
            crawl_api.post_crawl(crawl_api.CrawlRequest(channels=["tranco"]))
        assert e.value.status_code == 409
        assert spawned == [], f"已 busy 时不应 spawn: {spawned}"
    finally:
        crawl_api._JOBS_RUNNING.pop("inc-tranco", None)


def test_safe_db_path_rejects_symlink_outside_cwd(monkeypatch, tmp_path):
    """P1 修复（2026-09-30）：symlink 攻击防护——`data/leads.db` 即使含
    `..` 路径段会被挡，符号链接指向 /etc/passwd 等外部路径也挡。
    """
    import os
    fake_cwd = tmp_path / "fake_cwd"
    fake_cwd.mkdir()
    (fake_cwd / "data").mkdir()
    monkeypatch.setattr(crawl_api, "_CWD", fake_cwd)
    # 在 fake_cwd 外建一个目标，然后符号链接进 fake_cwd/data/
    external = tmp_path / "evil_target.txt"
    external.write_text("pwned")
    link = fake_cwd / "data" / "leads.db"
    os.symlink(external, link)
    try:
        # 即使 db_path 是"合法"的相对路径，resolve 后落到外部 → 拒绝
        with pytest.raises(HTTPException) as e:
            _safe_db_path("data/leads.db")
        assert e.value.status_code == 400
    finally:
        link.unlink()


def test_get_crawl_status_safe_iteration(monkeypatch):
    """2026-09-30 修复：`/api/crawl/status` TTL cleanup 必须用 `list(_JOBS_RUNNING.items())`
    快照——否则并发 pop 触发 RuntimeError 导致 API 500。
    """
    # 插入若干 stale job + active job
    now = 99999.0
    stale1 = crawl_api._Job(job_id="stale1", channel="tranco", pid=1,
                            started_at=now - 7200, proc=None, status="exited")  # type: ignore[arg-type]
    stale2 = crawl_api._Job(job_id="stale2", channel="play", pid=2,
                            started_at=now - 7200, proc=None, status="failed")  # type: ignore[arg-type]
    active = crawl_api._Job(job_id="active", channel="osm", pid=3,
                            started_at=now, proc=None, status="running")  # type: ignore[arg-type]
    crawl_api._JOBS_RUNNING.update({"stale1": stale1, "stale2": stale2, "active": active})
    try:
        import time as _t
        with patch.object(_t, "time", lambda: now):
            jobs = crawl_api.get_crawl_status()
        remaining = list(crawl_api._JOBS_RUNNING.keys())
        assert "active" in remaining
        assert "stale1" not in remaining and "stale2" not in remaining
    finally:
        crawl_api._JOBS_RUNNING.clear()
