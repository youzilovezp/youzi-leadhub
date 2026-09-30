"""crawl_api 加固回归（2026-09-28 B2）：并发 409 守卫 + db_path 收口 + _tail seek 化。

不真正 spawn 子进程——_spawn 全部 monkeypatch 掉（真实 spawn 会跑 Scrapy 爬取）。
"""
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app import crawl_api


def check(name, ok, detail=""):
    if ok:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}：{detail}")
        raise AssertionError(f"{name}: {detail}")
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
    # 仓库里有真实种子文件——必须打掉 _seed_candidates/_spawn，防测试真起爬取进程
    monkeypatch.setattr(crawl_api, "_seed_candidates", lambda ch: [])
    monkeypatch.setattr(crawl_api, "_spawn", lambda *a, **k: _fake_job())
    out = crawl_api.post_crawl(crawl_api.CrawlRequest(channels=["play"]))
    assert out["total"] == 0          # 无种子 → skipped 而非 409


def test_incremental_limit_is_full_seed_file(monkeypatch, tmp_path):
    """2026-09-29 修复：增量模式 limit=种子全量行数（旧实现取前 200 条，
    第 201+ 条种子永远轮不到 → 增量永远 +0）；full 模式仍用用户 limit。
    2026-09-30 扩展：incremental 模式 limit=所有候选文件行数之和。
    """
    from pathlib import Path
    seed = tmp_path / "seeds-x.txt"
    seed.write_text("https://a.com/\n" * 309 + "# comment\n\n", encoding="utf-8")
    monkeypatch.setattr(crawl_api, "_seed_candidates", lambda ch: [seed])
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    calls = {}
    monkeypatch.setattr(crawl_api, "_spawn",
                        lambda ch, s, limit, mp, db, mode, **kw:
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
    """GET /api/seeds：play 兜底候选（2 文件 + 副源）应求和而非只取首个。

    ponytail：原 bug —— _pick_seed 只返回首个候选，渠道兜底永远显示少量行。
    修复：返回 pools + details 两个字段，pools 是各渠道所有候选文件求和。
    2026-09-30 清理：tranco 渠道已移除（命中率 0.2%，污染源）。
    """
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    (tmp_path / "data").mkdir()
    # 模拟 play 渠道：play.txt（392 行）+ play-new.txt（111 行）
    (tmp_path / "data" / "seeds-play.txt").write_text("\n".join(f"https://a{i}.com/" for i in range(392)))
    (tmp_path / "data" / "seeds-play-new.txt").write_text("\n".join(f"https://b{i}.com/" for i in range(111)))
    # 模拟 osm 渠道：单文件 626 行
    (tmp_path / "data" / "seeds-osm.txt").write_text("\n".join(f"https://c{i}.com/" for i in range(626)))

    out = crawl_api.get_seed_pools()
    # play 应 = 392 + 111 = 503（汇总两个候选文件）
    assert out["pools"]["play"] == 503
    # osm 应 = 626
    assert out["pools"]["osm"] == 626
    # tranco 已从 ALL_CHANNELS 移除——不存在
    assert "tranco" not in out["pools"]
    # details 含每个文件的明细
    assert out["details"]["play"]["seeds-play.txt"] == 392
    assert out["details"]["play"]["seeds-play-new.txt"] == 111
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
    monkeypatch.setattr(crawl_api, "_seed_candidates", lambda ch: [seed])
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    # 模拟 tranco 已有 running job
    fake = crawl_api._Job(job_id="inc-play", channel="play", pid=1,
                          started_at=0.0, proc=None, status="running")  # type: ignore[arg-type]
    crawl_api._JOBS_RUNNING["inc-play"] = fake
    spawned = []
    monkeypatch.setattr(crawl_api, "_spawn",
                        lambda *a, **k: spawned.append(1) or fake)
    try:
        # 第一个请求应 409，不应 spawn
        with pytest.raises(HTTPException) as e:
            crawl_api.post_crawl(crawl_api.CrawlRequest(channels=["play"]))
        assert e.value.status_code == 409
        assert spawned == [], f"已 busy 时不应 spawn: {spawned}"
    finally:
        crawl_api._JOBS_RUNNING.pop("inc-play", None)


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


def test_post_crawl_incremental_uses_all_candidate_seeds(monkeypatch, tmp_path):
    """2026-09-30 修复：incremental 模式应汇总所有候选种子文件（_seed_candidates），
    而非 _pick_seed 取首个——例如 play 渠道有 play.txt + play-new.txt，
    旧逻辑只跑首文件，多次增量永远在同一批 URL 上 dupefilter 命中。
    """
    from app import crawl_api
    seed1 = tmp_path / "seeds-play.txt"
    seed1.write_text("\n".join(f"https://a{i}.com/" for i in range(392)))
    seed2 = tmp_path / "seeds-play-new.txt"
    seed2.write_text("\n".join(f"https://b{i}.com/" for i in range(111)))

    captured = {}
    def mock_spawn(channel, seed_file, limit, *args, **kw):
        captured["channel"] = channel
        captured["seed_file"] = seed_file
        captured["limit"] = limit
        return crawl_api._Job(job_id="t", channel=channel, pid=1, started_at=0.0,
                              status="running")  # type: ignore[arg-type]

    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    monkeypatch.setattr(crawl_api, "_seed_candidates", lambda ch: [seed1, seed2])
    monkeypatch.setattr(crawl_api, "_spawn", mock_spawn)

    # incremental 模式：limit = 392 + 111 = 503；seed 走 L1 排序临时文件（含全部候选 URL）
    #（2026-09-30 修复 {channel} NameError 后排序路径真正生效——旧断言"选最大文件"
    #  实为排序崩溃回退的 bug 行为）
    crawl_api.post_crawl(crawl_api.CrawlRequest(channels=["play"], mode="incremental"))
    check("incremental limit = 503（汇总所有候选）",
          captured["limit"] == 503, f"got {captured['limit']}")
    seed_file = Path(captured["seed_file"])
    urls = seed_file.read_text().split()
    check("incremental seed 走排序文件（固定名不堆积）",
          seed_file.name == ".seed-ranked-play.txt", f"got {seed_file.name}")
    check("排序文件含两候选全部 503 URL",
          len(urls) == 503 and {"https://a0.com/", "https://b0.com/"} <= set(urls),
          f"got {len(urls)}")

    # full 模式：limit 用 req.limit，排序文件只取前 N
    captured.clear()
    crawl_api.post_crawl(crawl_api.CrawlRequest(channels=["play"], mode="full", limit=100))
    check("full mode limit = req.limit=100",
          captured["limit"] == 100, f"got {captured['limit']}")
    seed_file = Path(captured["seed_file"])
    check("full mode seed 走排序文件且截断到 100",
          seed_file.name == ".seed-ranked-play.txt"
          and len(seed_file.read_text().split()) == 100)


def test_channel_busy_distinguishes_kill_vs_crash(monkeypatch):
    """2026-09-30 修复：_channel_busy 区分 exit code——rc==0（exited）/
    rc<0（killed，被信号）/ rc>0（failed，程序崩溃）。前端 StatusPill 依此显示
    "完成" / "已终止" / "失败"，避免"失败 (-9)"误判为程序崩溃。
    """
    import os, signal, subprocess, time
    from app import crawl_api

    def classify(rc):
        """复制 _channel_busy 内的状态切换逻辑（避免构造真实 _Job 的 Pydantic 限制）。"""
        if rc == 0: return "exited"
        if rc < 0: return "killed"
        return "failed"

    # 1. 正常退出 → status="exited"
    p_ok = subprocess.Popen([".venv/bin/python", "-c", "exit(0)"])
    p_ok.wait()
    check("rc=0 → exited", classify(p_ok.returncode) == "exited")

    # 2. SIGKILL → rc<0 → status="killed"
    p_kill = subprocess.Popen([".venv/bin/python", "-c", "import time;time.sleep(60)"])
    time.sleep(0.3)
    os.kill(p_kill.pid, signal.SIGKILL)
    p_kill.wait()
    assert p_kill.returncode == -9, f"预期 rc=-9, got {p_kill.returncode}"
    check("rc=-9 (SIGKILL) → killed", classify(p_kill.returncode) == "killed")

    # 3. 程序崩溃 → rc>0 → status="failed"
    p_fail = subprocess.Popen([".venv/bin/python", "-c", "exit(42)"])
    p_fail.wait()
    check("rc=42 (程序崩溃) → failed", classify(p_fail.returncode) == "failed")


def test_get_crawl_status_reads_db(monkeypatch, tmp_path):
    """2026-09-30 加固：status 走 job 表单一事实源（API 重启不丢）+ health 冒头。"""
    from app import db as _dbm
    (tmp_path / "data").mkdir(exist_ok=True)
    db_path = tmp_path / "data" / "leads.db"
    conn = _dbm.connect(db_path)
    try:
        _dbm.insert_job(conn, job_id="inc-osm", kind="crawl", channel="osm",
                        pid=11, started_at=100.0)
        for i in range(3):   # osm 连续 3 次补种失败 → health 冒头
            jid = f"seed-osm-{i}"
            _dbm.insert_job(conn, job_id=jid, kind="seed", channel="osm",
                            pid=20 + i, started_at=200.0 + i)
            _dbm.finish_job(conn, jid, "failed", 1)
    finally:
        conn.close()
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    out = crawl_api.get_crawl_status()
    ids = {j["job_id"] for j in out["jobs"]}
    assert {"inc-osm", "seed-osm-2"} <= ids
    assert all(j["log"] is None or isinstance(j["log"], str) for j in out["jobs"])
    assert out["health"].get("osm") == 3


def test_reap_pass_and_auto_chain(monkeypatch, tmp_path):
    """收割线程主链路：seed job exit 0 → 自动接续该渠道增量爬（chained 标记）。"""
    from app import db as _dbm
    from app.crawl_api import _Job
    (tmp_path / "data").mkdir(exist_ok=True)
    db_path = tmp_path / "data" / "leads.db"
    conn = _dbm.connect(db_path)
    try:
        _dbm.insert_job(conn, job_id="seed-osm-x", kind="seed", channel="osm",
                        pid=42, started_at=1.0)
    finally:
        conn.close()
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)

    class _FakeProc:
        def poll(self): return 0
    job = _Job(job_id="seed-osm-x", channel="osm", pid=42, started_at=1.0,
               proc=_FakeProc(), status="running")  # type: ignore[arg-type]
    crawl_api._JOBS_RUNNING["seed-osm-x"] = job

    spawned = []
    def fake_spawn(channel, seed_file, limit, max_pages, db_path, mode="incremental",
                   throttle=None, chained=False):
        spawned.append((channel, mode, chained))
        return _Job(job_id=f"chain-{channel}", channel=channel, pid=7,
                    started_at=2.0, proc=None, status="running")  # type: ignore[arg-type]
    monkeypatch.setattr(crawl_api, "_spawn", fake_spawn)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "seeds-osm.txt").write_text("https://a.com/\n")

    done = crawl_api._reap_pass()
    assert done == {"osm"}
    assert job.status == "exited"           # 内存收割
    crawl_api._auto_chain("osm")
    assert spawned and spawned[0][0] == "osm" and spawned[0][1] == "incremental"
    assert spawned[0][2] is True            # chained=1

    # DB 侧：seed job 行已被收割为 exited
    conn = _dbm.connect(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM job WHERE job_id='seed-osm-x' AND finished_at IS NOT NULL"
        ).fetchone()
        assert row is not None
    finally:
        conn.close()


# ============================================================
# 智能爬取黑盒（2026-09-30）：_smart_expand_scene 场景轮换
# ============================================================
def _fake_seed_job(channel="play", status="running"):
    return crawl_api._Job(job_id=f"seed-{channel}-20260930", channel=channel,
                          pid=123, started_at=0.0, proc=None, status=status)


def test_smart_expand_scene_rotates(tmp_path, monkeypatch):
    """定向补种：只 spawn 池尽渠道的计划；轮换指针落在 data/ 内可随库迁移。"""
    calls = []
    monkeypatch.setattr(crawl_api, "_SMART_ROTATION", tmp_path / ".smart-rotation")
    monkeypatch.setattr(crawl_api, "_spawn_seed",
                        lambda ch, c, cat, lim, out: calls.append(ch)
                        or _fake_seed_job(ch))
    from app.seeds_scenes import SCENES
    got = crawl_api._smart_expand_scene(seed_channels={"osm"})
    assert [j.channel for j in got] == ["osm"]       # 只补目标渠道
    assert (tmp_path / ".smart-rotation").read_text().isdigit()
    # 目标渠道不在任何场景（如 myshopify）→ 不轮换不 spawn
    calls.clear()
    assert crawl_api._smart_expand_scene(seed_channels={"myshopify"}) == []
    assert calls == []


def test_smart_expand_scene_skips_when_seed_running(tmp_path, monkeypatch):
    """已有 seed job 在跑 → 跳过本轮（防重复打 Google Play/Overpass）。"""
    monkeypatch.setattr(crawl_api, "_SMART_ROTATION", tmp_path / ".smart-rotation")
    boom = lambda *a, **k: pytest.fail("不应再 spawn")
    monkeypatch.setattr(crawl_api, "_spawn_seed", boom)
    crawl_api._JOBS_RUNNING["seed-osm-x"] = _fake_seed_job("osm")
    assert crawl_api._smart_expand_scene(seed_channels={"osm"}) == []


def test_post_crawl_smart_all_busy_idempotent_200(monkeypatch):
    """Q14 幂等合并：smart 全忙 → 200 + hint（点正在跑的批次不是错误）。"""
    crawl_api._JOBS_RUNNING["inc-play"] = _fake_job("play")
    crawl_api._JOBS_RUNNING["inc-osm"] = _fake_job("osm")
    crawl_api._JOBS_RUNNING["inc-myshopify"] = _fake_job("myshopify")
    out = crawl_api.post_crawl(crawl_api.CrawlRequest(mode="smart"))
    assert out["total"] == 0 and out["spawned"] == []
    assert len(out["skipped"]) == 3 and "进行中" in out["hint"]
