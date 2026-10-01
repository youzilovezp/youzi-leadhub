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
    # latam_ecom 含 play + itunes + osm 三个 plan → 三条 cmd
    assert len(cmds) == 3
    play_cmd = " ".join(next(c for c in cmds if "play" in c))
    osm_cmd = " ".join(next(c for c in cmds if "osm" in c))
    itunes_cmd = " ".join(next(c for c in cmds if "itunes" in c))
    assert "--append" in play_cmd and "--country" in play_cmd
    assert "br,mx" in play_cmd and "SHOPPING" in play_cmd
    assert "--append" in osm_cmd and "--country" in osm_cmd
    # itunes 与 play 同为 App 渠道：按类目分榜取种子
    assert "--append" in itunes_cmd and "--category" in itunes_cmd
    assert "SHOPPING" in itunes_cmd
    # 返回结构：scene + jobs + targets
    assert out["scene"]["id"] == "latam_ecom"
    assert len(out["jobs"]) == 3
    assert all(j["status"] == "running" for j in out["jobs"])
    # 清掉 fake job 避免 id_food 因 osm busy 撞 409
    crawl_api._JOBS_RUNNING.clear()
    # 未知场景 → 400
    with pytest.raises(HTTPException) as e:
        crawl_api.post_seeds(crawl_api.SeedsRequest(scene="nope"))
    assert e.value.status_code == 400
    # id_food 场景（osm + itunes）→ 两条 cmd
    cmds.clear()
    crawl_api.post_seeds(crawl_api.SeedsRequest(scene="id_food"))
    assert len(cmds) == 2 and any("osm" in c for c in cmds) \
        and any("itunes" in c for c in cmds)


def test_get_scenes_returns_business_scenes():
    """GET /api/scenes 返回 5 个业务场景供前端卡片渲染。"""
    from app.seeds_scenes import SCENES
    out = crawl_api.get_scenes()
    assert len(out) == len(SCENES)
    for s, item in zip(SCENES, out):
        assert item["id"] == s.id
        assert item["label"] == s.label
        # 每场景含 1-3 渠道（play/itunes/osm）
        assert 1 <= len(item["channels"]) <= 3
        assert set(item["channels"]).issubset({"play", "itunes", "osm"})


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
    # sea_smb: play 5 国, per_country=250（2026-10-01 燃料扩容 150→250）
    crawl_api.post_seeds(crawl_api.SeedsRequest(scene="sea_smb"))
    play_cmd = " ".join(next(c for c in cmds if "play" in c))
    # 关键断言：--limit 应是 per_country=250，不是 250 × 5 = 1250
    assert "--limit 250" in play_cmd, f"play --limit 错误（应=per_country=250）: {play_cmd}"


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


def test_channel_busy_db_fallback_for_restart_orphans(monkeypatch, tmp_path):
    """2026-10-01 P1 修复：API 重启后内存 _JOBS_RUNNING 清空，孤儿爬取子进程
    （start_new_session 脱离进程组）仍活着、job 表仍是 running——旧 _channel_busy
    只扫内存 → 判不忙 → 再 spawn 同 _inc JOBDIR，双进程互毁指纹/队列。
    修复：内存判不忙后兜底查 job 表 running 行 + pid 活性。
    """
    import subprocess
    import sys
    from app import db as _dbm
    (tmp_path / "data").mkdir(exist_ok=True)
    db_path = tmp_path / "data" / "leads.db"
    # 真活进程当"孤儿爬取子进程"（本测试进程外、脱离进程组语义）
    orphan = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    conn = _dbm.connect(db_path)
    try:
        _dbm.insert_job(conn, job_id="inc-play", kind="crawl", channel="play",
                        pid=orphan.pid, started_at=1.0)
    finally:
        conn.close()
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    try:
        check("活孤儿 running 行 → busy（DB 兜底拦住重启窗口双爬）",
              crawl_api._channel_busy("play") is True)
        check("无 running 行的其他渠道 → 不 busy",
              crawl_api._channel_busy("osm") is False)
    finally:
        orphan.kill()
        orphan.wait()
    # 孤儿死后：pid 已死 → 不 busy（该行留给 reaper 的 _reap_lost_jobs 收割）
    check("孤儿退出后 → 不 busy（不永久卡死渠道）",
          crawl_api._channel_busy("play") is False)


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
    (tmp_path / "data").mkdir(exist_ok=True)   # _CWD 隔离（_channel_busy DB 兜底）
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    monkeypatch.setattr(crawl_api, "_SMART_ROTATION", tmp_path / ".smart-rotation")
    monkeypatch.setattr(crawl_api, "_spawn_seed",
                        lambda ch, c, cat, lim, out: calls.append(ch)
                        or _fake_seed_job(ch))
    monkeypatch.setattr(crawl_api, "_pick_seed",
                        lambda ch: f"data/seeds-{ch}.txt")
    # 不让 streak 真实数据影响本测试（实跑库可能有历史失败）
    monkeypatch.setattr(crawl_api._db, "seed_fail_streaks",
                        lambda conn, window=3: {})
    got = crawl_api._smart_expand_scene(seed_channels={"osm"})
    assert [j.channel for j in got] == ["osm"]       # 只补目标渠道
    assert (tmp_path / ".smart-rotation").read_text().isdigit()
    # myshopify 不在任何 SCENES plans 里 → 走兜底路径（2026-10-01 修复）
    calls.clear()
    got2 = crawl_api._smart_expand_scene(seed_channels={"myshopify"})
    assert [j.channel for j in got2] == ["myshopify"]
    assert calls == ["myshopify"]


def test_smart_expand_scene_skips_when_seed_running(tmp_path, monkeypatch):
    """已有 seed job 在跑 → 跳过本轮（防重复打 Google Play/Overpass）。"""
    (tmp_path / "data").mkdir(exist_ok=True)   # _CWD 隔离（_channel_busy DB 兜底）
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    monkeypatch.setattr(crawl_api, "_SMART_ROTATION", tmp_path / ".smart-rotation")
    boom = lambda *a, **k: pytest.fail("不应再 spawn")
    monkeypatch.setattr(crawl_api, "_spawn_seed", boom)
    crawl_api._JOBS_RUNNING["seed-osm-x"] = _fake_seed_job("osm")
    assert crawl_api._smart_expand_scene(seed_channels={"osm"}) == []


def test_smart_expand_scene_fallback_covers_myshopify(tmp_path, monkeypatch):
    """2026-10-01 修复：myshopify 不在任何 SCENES plans 里——之前 starve 永远
    没人补。智能爬取时若只 myshopify starved → 走兜底 spawn（country=''/category=''
    ——CDX 通配 *.myshopify.com，不需要）。"""
    spawns = []
    (tmp_path / "data").mkdir(exist_ok=True)   # _CWD 隔离（_channel_busy DB 兜底）
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    monkeypatch.setattr(crawl_api, "_SMART_ROTATION", tmp_path / ".smart-rotation")
    monkeypatch.setattr(crawl_api, "_pick_seed",
                        lambda ch: f"data/seeds-{ch}.txt")
    # 不让 streak 真实数据影响本测试（实跑库可能有历史失败）
    monkeypatch.setattr(crawl_api._db, "seed_fail_streaks",
                        lambda conn, window=3: {})
    def fake_seed(channel, countries, categories, limit, out_file):
        spawns.append((channel, countries, categories, limit))
        return _fake_seed_job(channel)
    monkeypatch.setattr(crawl_api, "_spawn_seed", fake_seed)
    got = crawl_api._smart_expand_scene(seed_channels={"myshopify"})
    assert [j.channel for j in got] == ["myshopify"]
    ch, countries, categories, limit = spawns[0]
    assert ch == "myshopify"
    assert countries == ""          # CDX 无国家概念
    assert categories == ""         # myshopify 不用 category
    assert limit == 500


def test_smart_expand_scene_backoff_on_seed_streak(tmp_path, monkeypatch):
    """2026-10-01 修复：seed 失败 streak ≥ 2 → 跳过（避免 Overpass 挂掉时无限
    spawn 一个失败的 OSM seed；之前完全无退避，每次点击都 24 分钟挂死）。"""
    from app import db as _dbm
    (tmp_path / "data").mkdir(exist_ok=True)
    db_path = tmp_path / "data" / "leads.db"
    conn = _dbm.connect(db_path)
    try:
        # 注入 2 条连续失败（streak=2 触发跳过）
        for i in range(2):
            jid = f"seed-osm-{i}"
            _dbm.insert_job(conn, job_id=jid, kind="seed", channel="osm",
                            pid=20 + i, started_at=200.0 + i)
            _dbm.finish_job(conn, jid, "failed", 1)
    finally:
        conn.close()
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    monkeypatch.setattr(crawl_api, "_SMART_ROTATION", tmp_path / ".smart-rotation")
    spawn_calls = []
    monkeypatch.setattr(crawl_api, "_spawn_seed",
                        lambda ch, *a, **k: spawn_calls.append(ch)
                        or _fake_seed_job(ch))
    # osm streak≥2 → 跳过（filter 在最前面，连 seed 也不 spawn）
    assert crawl_api._smart_expand_scene(seed_channels={"osm"}) == []
    assert "osm" not in spawn_calls
    # 没失败记录的渠道（如 myshopify，走兜底路径）→ 仍可 spawn
    got = crawl_api._smart_expand_scene(seed_channels={"myshopify"})
    assert [j.channel for j in got] == ["myshopify"]
    assert "myshopify" in spawn_calls


def test_seed_backoff_ttl_recovers(tmp_path, monkeypatch):
    """2026-10-01 修复：补种 streak 剔除加 30min TTL——旧逻辑 streak≥2 永久
    静默剔除（Overpass 间歇故障后渠道永不自愈，无 skipped 解释）。"""
    import time as _t
    from app import db as _dbm
    (tmp_path / "data").mkdir(exist_ok=True)
    conn = _dbm.connect(tmp_path / "data" / "leads.db")
    try:
        for i in range(2):
            _dbm.insert_job(conn, job_id=f"seed-osm-{i}", kind="seed",
                            channel="osm", pid=20 + i, started_at=200.0 + i)
            _dbm.finish_job(conn, f"seed-osm-{i}", "failed", 1)
    finally:
        conn.close()
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    check("刚失败 streak=2 → 退避中", crawl_api._seed_backing_off("osm", {"osm": 2}) is True)
    conn = _dbm.connect(tmp_path / "data" / "leads.db")
    conn.execute("UPDATE job SET finished_at=? WHERE kind='seed'",
                 (_t.time() - 31 * 60,))
    conn.commit()
    conn.close()
    check("31min 前失败 → 放行重试（TTL 自愈）",
          crawl_api._seed_backing_off("osm", {"osm": 2}) is False)
    check("streak<2 → 不退避", crawl_api._seed_backing_off("osm", {"osm": 1}) is False)


def test_post_crawl_smart_all_busy_idempotent_200(monkeypatch):
    """Q14 幂等合并：smart 全忙 → 200 + hint（点正在跑的批次不是错误）。"""
    crawl_api._JOBS_RUNNING["inc-play"] = _fake_job("play")
    crawl_api._JOBS_RUNNING["inc-osm"] = _fake_job("osm")
    crawl_api._JOBS_RUNNING["inc-itunes"] = _fake_job("itunes")
    out = crawl_api.post_crawl(crawl_api.CrawlRequest(mode="smart"))
    assert out["total"] == 0 and out["spawned"] == []
    assert len(out["skipped"]) == 3 and "进行中" in out["hint"]


def test_smart_expand_scene_covers_itunes(tmp_path, monkeypatch):
    """2026-10-01 新渠道 itunes 进智能补种：itunes 池尽时场景轮换能选到覆盖
    它的场景并 spawn（--country/--category 参数与 play 同通道）。"""
    spawns = []
    # _CWD 必须隔离：_channel_busy 的 DB 兜底会查 job 表——不隔离时读到真实
    # 库里正在跑的任务（如线上 itunes seed），渠道被误判 busy → 测试随生产波动
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    monkeypatch.setattr(crawl_api, "_SMART_ROTATION", tmp_path / ".smart-rotation")
    monkeypatch.setattr(crawl_api, "_pick_seed", lambda ch: f"data/seeds-{ch}.txt")
    monkeypatch.setattr(crawl_api._db, "seed_fail_streaks",
                        lambda conn, window=3: {})

    def fake_seed(channel, countries, categories, limit, out_file):
        spawns.append((channel, countries, categories))
        return _fake_seed_job(channel)
    monkeypatch.setattr(crawl_api, "_spawn_seed", fake_seed)
    got = crawl_api._smart_expand_scene(seed_channels={"itunes"})
    assert [j.channel for j in got] == ["itunes"]
    ch, countries, categories = spawns[0]
    assert ch == "itunes" and countries and categories, (ch, countries, categories)


def test_post_crawl_smart_starved_seeds_crawlable_crawls(monkeypatch, tmp_path):
    """2026-09-30 审计：smart 模式增量闭环决策。

    池尽渠道（unseen 估计 < 10000）→ 转补种不空爬；池未尽渠道 → 照爬；
    请求内渠道去重（["play","play"] 不允许同 JOBDIR 双 spawn）。

    2026-10-01 更新：阈值从 100 提到 10000（"必重访"语义：每次点击都补种尝试），
    所以测试里 play 用 50000 unseen 表达"非 starved"状态。
    """
    from app import crawl_api
    (tmp_path / "data").mkdir()
    seed = tmp_path / "seeds-play.txt"
    seed.write_text("https://a1.com/\nhttps://a2.com/\n")

    expand_calls: list[set] = []
    crawl_calls: list[str] = []

    def fake_expand(seed_channels):
        expand_calls.append(set(seed_channels))
        return [crawl_api.JobInfo(job_id="seed-osm-x", channel="osm", pid=2,
                                  started_at=0.0, status="running")]  # type: ignore[arg-type]

    def fake_spawn(channel, *a, **kw):
        crawl_calls.append(channel)
        return crawl_api._Job(job_id=f"inc-{channel}", channel=channel, pid=1,
                              started_at=0.0, status="running")  # type: ignore[arg-type]

    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    # play unseen=50000 → 池未尽（crawlable）；osm unseen=5 → 池尽（starved）
    monkeypatch.setattr(crawl_api, "_unseen_estimate",
                        lambda ch: 5 if ch == "osm" else 50000)
    monkeypatch.setattr(crawl_api, "_smart_expand_scene", fake_expand)
    monkeypatch.setattr(crawl_api, "_spawn", fake_spawn)
    monkeypatch.setattr(crawl_api, "_pick_recrawl_target",
                        lambda spawned: None)  # 测试里关闭 re-crawl 干扰
    monkeypatch.setattr(crawl_api, "_seed_candidates",
                        lambda ch: [seed] if ch == "play" else [])

    r = crawl_api.post_crawl(crawl_api.CrawlRequest(
        channels=["play", "play", "osm"], mode="smart"))

    check("池尽渠道 osm → 转补种", expand_calls == [{"osm"}], f"got {expand_calls}")
    check("池未尽渠道 play → 爬取且去重（play 传了两次只爬一次）",
          crawl_calls == ["play"], f"got {crawl_calls}")
    check("响应回传补种数 seeded=1", r.get("seeded") == 1, f"got {r.get('seeded')}")


def test_post_crawl_smart_spawns_recrawl(monkeypatch, tmp_path):
    """2026-10-01「必重访」：smart 模式除正常 spawn 外，额外 spawn 一个 mode=full
    的 re-crawl（用新 JOBDIR 跳过 dupefilter），目标是访问已爬过的实体找新挂的
    wa.me。验证：spawned 包含至少一个 mode=full 的 job。"""
    from app import crawl_api
    (tmp_path / "data").mkdir()
    seed = tmp_path / "seeds-play.txt"
    seed.write_text("\n".join(f"https://a{i}.com/" for i in range(500)))

    spawned_modes: list[tuple[str, str]] = []
    def fake_spawn(channel, seed_file, limit, max_pages, db_path, mode="incremental",
                   throttle=None, chained=False):
        spawned_modes.append((channel, mode))
        return crawl_api._Job(job_id=f"{mode}-{channel}", channel=channel, pid=1,
                              started_at=0.0, status="running")  # type: ignore[arg-type]
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    monkeypatch.setattr(crawl_api, "_seed_candidates", lambda ch: [seed] if ch == "play" else [])
    monkeypatch.setattr(crawl_api, "_smart_expand_scene", lambda seed_channels: [])
    # play unseen=50000 → 池未尽 → 走 crawlable（play 进 spawned）；starved 兜底不会触发
    monkeypatch.setattr(crawl_api, "_unseen_estimate", lambda ch: 50000)
    monkeypatch.setattr(crawl_api, "_spawn", fake_spawn)
    monkeypatch.setattr(crawl_api._db, "seed_fail_streaks", lambda conn, window=3: {})
    # 给 play 一个高 attempts 让 _pick_recrawl_target 选中
    monkeypatch.setattr(crawl_api._db, "get_crawl_stats",
                        lambda conn, ch: {"attempts": 100, "hits": 20})

    r = crawl_api.post_crawl(crawl_api.CrawlRequest(channels=["play"], mode="smart"))
    # 应该至少有：play incremental + play full (re-crawl)
    full_modes = [(ch, m) for ch, m in spawned_modes if m == "full"]
    check("smart 模式 spawn 含 mode=full 的 re-crawl",
          len(full_modes) >= 1, f"got {spawned_modes}")
    check("re-crawl 用 spawn 中 attempts 最大的渠道（play）",
          full_modes[0][0] == "play" if full_modes else False,
          f"got {full_modes}")


def test_post_crawl_smart_all_cooling_falls_back_to_full_recrawl(monkeypatch, tmp_path):
    """2026-10-01 P0-2 修复：全冷却窗口点击 0 任务假承诺。

    smart 故意选冷却渠道「给恢复机会」（ok or [ranked[0]]），主循环又无条件按
    冷却 skip → spawned=[] 且 seeded=[]，旧代码 total=0 且无任何后台任务（前端
    却提示"本批进行中"）。修复：0-spawn 且 0-seed 时兜底走 full 重访——该路径
    不查冷却、能产首 sighting，保证「点击→必有任务」闭环。
    """
    from datetime import datetime, timedelta, timezone
    (tmp_path / "data").mkdir()
    seed = tmp_path / "seeds-play.txt"
    seed.write_text("\n".join(f"https://a{i}.com/" for i in range(300)))

    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    spawned_modes: list[tuple[str, str]] = []

    def fake_spawn(channel, *a, **kw):
        mode = a[4] if len(a) > 4 else kw.get("mode", "incremental")
        spawned_modes.append((channel, mode))
        return crawl_api._Job(job_id=f"{mode}-{channel}", channel=channel, pid=1,
                              started_at=0.0, status="running")  # type: ignore[arg-type]

    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    monkeypatch.setattr(crawl_api, "_seed_candidates",
                        lambda ch: [seed] if ch == "play" else [])
    monkeypatch.setattr(crawl_api, "_smart_expand_scene", lambda seed_channels: [])
    monkeypatch.setattr(crawl_api, "_unseen_estimate", lambda ch: 50000)
    monkeypatch.setattr(crawl_api, "_spawn", fake_spawn)
    # 主循环冷却判定与 _pick_recrawl_target 共用：attempts 100（≥50 可重访）
    # + backoff_until 未来 5 分钟（主循环 skip 的根因）
    monkeypatch.setattr(crawl_api._db, "get_crawl_stats",
                        lambda conn, ch: {"attempts": 100, "hits": 20,
                                          "backoff_until": future})

    r = crawl_api.post_crawl(crawl_api.CrawlRequest(channels=["play"], mode="smart"))
    check("全冷却窗口 smart 兜底 full 重访（不再 0 任务）",
          ("play", "full") in spawned_modes, f"got {spawned_modes}")
    check("响应 total ≥ 1", r["total"] >= 1, f"got {r['total']}")
    # 主循环的冷却 skip 仍要生效（不允许 incremental 走 _inc JOBDIR 硬闯冷却）
    check("冷却渠道不被 incremental 硬爬",
          ("play", "incremental") not in spawned_modes, f"got {spawned_modes}")


def test_post_crawl_smart_recrawl_uses_ranked_seed(monkeypatch, tmp_path):
    """2026-10-01 修复：smart「必重访」吃原始种子文件前 N 行（每次同一批 URL
    反复重访）→ 改走 L1 排序（高历史产出优先），文件=.seed-ranked-<ch>.txt。"""
    from app import db as _dbm
    (tmp_path / "data").mkdir()
    seed = tmp_path / "seeds-play.txt"
    seed.write_text("\n".join(f"https://a{i}.com/" for i in range(500)))

    conn = _dbm.connect(tmp_path / "data" / "leads.db")
    try:
        # a499.com 历史高分 → 排序后应排重访名单最前
        _dbm.upsert_domain(conn, "a499.com", "play", None)
        conn.execute("UPDATE domain SET score=10 WHERE entity_key='a499.com'")
        conn.commit()
    finally:
        conn.close()

    def fake_spawn(channel, *a, **kw):
        mode = a[4] if len(a) > 4 else kw.get("mode", "incremental")
        return crawl_api._Job(job_id=f"{mode}-{channel}", channel=channel, pid=1,
                              started_at=0.0, status="running")  # type: ignore[arg-type]

    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    monkeypatch.setattr(crawl_api, "_seed_candidates",
                        lambda ch: [seed] if ch == "play" else [])
    monkeypatch.setattr(crawl_api, "_smart_expand_scene", lambda seed_channels: [])
    monkeypatch.setattr(crawl_api, "_unseen_estimate", lambda ch: 50000)
    monkeypatch.setattr(crawl_api._db, "get_crawl_stats",
                        lambda conn, ch: {"attempts": 100, "hits": 20})
    monkeypatch.setattr(crawl_api, "_spawn", fake_spawn)

    crawl_api.post_crawl(crawl_api.CrawlRequest(channels=["play"], mode="smart"))
    ranked = tmp_path / "data" / ".seed-ranked-play.txt"
    check("重访走排序文件", ranked.exists(), "文件未生成")
    urls = ranked.read_text().split()
    # incremental(limit=500) 先写、full 重访(limit=100) 后写覆盖 → 100 行
    check("重访名单截断到 rc_limit=100", len(urls) == 100, f"got {len(urls)}")
    check("高历史产出域排最前", urls[0] == "https://a499.com/", f"got {urls[0]}")


def test_post_crawl_smart_recrawl_time_gate(monkeypatch, tmp_path):
    """2026-10-01 修复：smart 每次点击都 full 重访（无频控）→ 6h 时间闸。
    6h 内有 full 重访记录 → 不再重访；超过 6h → 放行。"""
    import time as _time
    from app import db as _dbm
    (tmp_path / "data").mkdir()
    seed = tmp_path / "seeds-play.txt"
    seed.write_text("\n".join(f"https://a{i}.com/" for i in range(500)))

    def _mark_full_finished(hours_ago: float):
        conn = _dbm.connect(tmp_path / "data" / "leads.db")
        try:
            _dbm.insert_job(conn, job_id="full-play-x", kind="crawl", channel="play",
                            pid=999, started_at=1.0)
            _dbm.finish_job(conn, "full-play-x", "exited", 0)
            conn.execute("UPDATE job SET finished_at=? WHERE job_id='full-play-x'",
                         (_time.time() - hours_ago * 3600,))
            conn.commit()
        finally:
            conn.close()

    spawned_modes: list[tuple[str, str]] = []

    def fake_spawn(channel, *a, **kw):
        mode = a[4] if len(a) > 4 else kw.get("mode", "incremental")
        spawned_modes.append((channel, mode))
        return crawl_api._Job(job_id=f"{mode}-{channel}", channel=channel, pid=1,
                              started_at=0.0, status="running")  # type: ignore[arg-type]

    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    monkeypatch.setattr(crawl_api, "_seed_candidates",
                        lambda ch: [seed] if ch == "play" else [])
    monkeypatch.setattr(crawl_api, "_smart_expand_scene", lambda seed_channels: [])
    monkeypatch.setattr(crawl_api, "_unseen_estimate", lambda ch: 50000)
    monkeypatch.setattr(crawl_api._db, "get_crawl_stats",
                        lambda conn, ch: {"attempts": 100, "hits": 20})
    monkeypatch.setattr(crawl_api, "_spawn", fake_spawn)

    _mark_full_finished(1)   # 1h 前刚 full 重访过
    crawl_api.post_crawl(crawl_api.CrawlRequest(channels=["play"], mode="smart"))
    check("6h 内不重复 full 重访",
          ("play", "full") not in spawned_modes, f"got {spawned_modes}")
    check("incremental 照常（闸只挡重访）",
          ("play", "incremental") in spawned_modes, f"got {spawned_modes}")

    spawned_modes.clear()
    crawl_api._JOBS_RUNNING.clear()
    _mark_full_finished(7)   # 覆盖同一 job_id 行，改为 7h 前
    crawl_api.post_crawl(crawl_api.CrawlRequest(channels=["play"], mode="smart"))
    check("超过 6h 放行 full 重访",
          ("play", "full") in spawned_modes, f"got {spawned_modes}")


def test_post_crawl_smart_no_recrawl_when_attempts_low(monkeypatch, tmp_path):
    """2026-10-01 边界：spawned 渠道的 attempts < 50 时不触发 re-crawl
    （新渠道刚起步，重访 0 产出的实体没意义）。"""
    from app import crawl_api
    (tmp_path / "data").mkdir()
    seed = tmp_path / "seeds-play.txt"
    seed.write_text("https://a.com/\n")

    spawned_modes: list[str] = []
    def fake_spawn(channel, *a, **kw):
        mode = a[4] if len(a) > 4 else kw.get("mode", "incremental")
        spawned_modes.append(mode)
        return crawl_api._Job(job_id=f"{mode}-{channel}", channel=channel, pid=1,
                              started_at=0.0, status="running")  # type: ignore[arg-type]
    monkeypatch.setattr(crawl_api, "_CWD", tmp_path)
    monkeypatch.setattr(crawl_api, "_seed_candidates", lambda ch: [seed] if ch == "play" else [])
    monkeypatch.setattr(crawl_api, "_smart_expand_scene", lambda seed_channels: [])
    monkeypatch.setattr(crawl_api, "_unseen_estimate", lambda ch: 50000)
    # crawl_stats: play attempts=10（< 50） → 不该 re-crawl
    monkeypatch.setattr(crawl_api._db, "get_crawl_stats",
                        lambda conn, ch: {"attempts": 10, "hits": 0})
    monkeypatch.setattr(crawl_api._db, "seed_fail_streaks",
                        lambda conn, window=3: {})
    monkeypatch.setattr(crawl_api, "_spawn", fake_spawn)

    crawl_api.post_crawl(crawl_api.CrawlRequest(channels=["play"], mode="smart"))
    check("attempts < 50 不触发 full re-crawl",
          "full" not in spawned_modes, f"got {spawned_modes}")
