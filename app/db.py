"""SQLite 存储 —— 三表 schema（报告 3.4）。

ponytail: MVP 用 SQLite（stdlib、零部署），SQL 保持 PG 兼容；
迁 PG 时只换连接层（ON CONFLICT 语法 PG 同名支持）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS domain (
  entity_key      TEXT PRIMARY KEY,        -- eTLD+1；平台公共后缀(myshopify.com 等 PSL 私有段)回退完整 host
  channel         TEXT NOT NULL,           -- 种子渠道: tranco / myshopify / play / sample
  seed_host       TEXT,                    -- 种子来源 host（重定向前）
  developer_name  TEXT,                    -- 种子来源开发者主体名（play 渠道独有；P0 第 1 强信号）
  status          TEXT NOT NULL DEFAULT 'pending',  -- pending|scored|redirected|blocked|error
  widget          TEXT,                    -- 站内 widget 指纹（无号码也记录，作 diff 特征）
  p0              INTEGER NOT NULL DEFAULT 0,
  market          TEXT,                    -- 号码国家（ISO 地区码）
  market_group    TEXT,                    -- 市场分组标签（SEA/LATAM/MENA/EU/OTHER）
  lang            TEXT,
  score           INTEGER NOT NULL DEFAULT 0,
  first_seen      TEXT NOT NULL,
  last_crawled    TEXT
);
CREATE TABLE IF NOT EXISTS phone (
  e164    TEXT PRIMARY KEY,
  valid   INTEGER NOT NULL DEFAULT 1,
  country TEXT
);
CREATE TABLE IF NOT EXISTS sighting (
  entity_key TEXT NOT NULL REFERENCES domain(entity_key),
  e164       TEXT NOT NULL REFERENCES phone(e164),
  url        TEXT NOT NULL,             -- 合规留痕：来源 URL + 日期（报告 7.4）
  layer      TEXT NOT NULL,             -- link | text
  first_seen TEXT NOT NULL,
  last_seen  TEXT NOT NULL,
  PRIMARY KEY (entity_key, e164, url)
);
CREATE INDEX IF NOT EXISTS idx_domain_channel ON domain(channel);
-- sighting 无需单列 entity_key 索引：PK(entity_key,e164,url) 的自动索引已前缀覆盖
-- 且是覆盖索引（EXPLAIN 验证），少维护一个索引 = 热路径 upsert 更快
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str | Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # 2026-10-01 修复：多进程并发写 WAL（爬取子进程 × N + API + enrich）默认锁
    # 等待仅 5s——score_pending 长事务持写锁期间其他进程直接抛 "database is
    # locked" 丢 item（喂出幽灵 unseen）。15s 让写者排队而非失败。
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量列迁移：CREATE IF NOT EXISTS 不改旧表——缺列补上（D1-3 后新增打分持久化列）。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(domain)")}
    for col in ("icp", "hreflang_zh", "title_zh", "gambling"):
        if col not in cols:
            conn.execute(f"ALTER TABLE domain ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
    if "email" not in cols:
        conn.execute("ALTER TABLE domain ADD COLUMN email TEXT")  # 富化：站内 mailto（Q8）
    if "developer_name" not in cols:
        conn.execute("ALTER TABLE domain ADD COLUMN developer_name TEXT")  # P0 第 1 强信号
    if "market_group" not in cols:
        conn.execute("ALTER TABLE domain ADD COLUMN market_group TEXT")    # 市场分组标签

    # 2026-09-30 付费扩展性预留：5 列，零业务影响；接入第一个付费 API 时直接读
    # enrichment_status / contact_email / tech_signals / outreach_message，不动 schema
    if "enrichment_status" not in cols:
        conn.execute("ALTER TABLE domain ADD COLUMN enrichment_status TEXT NOT NULL DEFAULT 'none'")
    if "enriched_at" not in cols:
        conn.execute("ALTER TABLE domain ADD COLUMN enriched_at TEXT")
    if "contact_email" not in cols:
        conn.execute("ALTER TABLE domain ADD COLUMN contact_email TEXT")  # 付费 API 补全企业邮箱（与 email=mailto 语义不同）
    if "tech_signals" not in cols:
        conn.execute("ALTER TABLE domain ADD COLUMN tech_signals TEXT")    # JSON：cms/ecommerce/cdn 等（BuiltWith/Wappalyzer 接入）
    if "outreach_message" not in cols:
        conn.execute("ALTER TABLE domain ADD COLUMN outreach_message TEXT")  # LLM 开场白草稿（D6+）

    # 2026-09-30 销售 UX 重构：跟进状态字段（销售每天工作流核心）
    # 状态机：new → contacted → in_conversation → won / lost
    if "contact_status" not in cols:
        conn.execute("ALTER TABLE domain ADD COLUMN contact_status TEXT NOT NULL DEFAULT 'new'")
    if "contacted_at" not in cols:
        conn.execute("ALTER TABLE domain ADD COLUMN contacted_at TEXT")
    if "contact_notes" not in cols:
        conn.execute("ALTER TABLE domain ADD COLUMN contact_notes TEXT")

    # E2 fix: 索引补充（按 v7 spec 高频查询路径）
    # 单条 ALTER 后即时建索引——>10k 行后无索引会让 /api/leads 按 market_group 扫表
    conn.execute("CREATE INDEX IF NOT EXISTS idx_domain_market_group ON domain(market_group)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_domain_score ON domain(score DESC)")
    # 2026-09-30 修复：复合索引 — /api/leads?market_group=X ORDER BY score DESC
    # 走单列 market_group 后 post-sort，10 万行扫描后再排是瓶颈
    conn.execute("CREATE INDEX IF NOT EXISTS idx_domain_market_score "
                 "ON domain(market_group, score DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_domain_p0 ON domain(p0) WHERE p0 = 1")  # 部分索引，小
    # 销售高频查询：今日队列 = (contact_status='new' AND p0=1) ORDER BY score DESC
    conn.execute("CREATE INDEX IF NOT EXISTS idx_domain_today_queue "
                 "ON domain(contact_status, p0, score DESC) "
                 "WHERE contact_status = 'new'")

    # 2026-09-30 智能爬虫（Smart Crawler）：每 channel ROI + 错误连击 + 冷却窗口
    if "crawl_stats" not in {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}:
        conn.execute(
            """CREATE TABLE crawl_stats (
                 channel TEXT PRIMARY KEY,
                 attempts INT NOT NULL DEFAULT 0,    -- 累计爬过 URL 数
                 hits INT NOT NULL DEFAULT 0,         -- 累计入库 entity 数（含 WA）
                 phones INT NOT NULL DEFAULT 0,       -- 累计发现 WA 号码数
                 errors INT NOT NULL DEFAULT 0,       -- 累计 5xx/timeout/4xx 计数
                 last_crawl_at TEXT,                  -- 上次爬取时间
                 last_hit_at TEXT,                    -- 上次入库时间（用于"最近有产出吗"）
                 consecutive_errors INT NOT NULL DEFAULT 0, -- 当前错误连击
                 backoff_until TEXT,                  -- 冷却到该时刻之前不爬
                 updated_at TEXT NOT NULL DEFAULT (datetime('now'))
               )""")

    # 2026-09-30 智能爬取加固：job 表 = 任务状态单一事实源（API 重启不丢）
    # kind: 'crawl' | 'seed'；chained=1 表示由收割线程自动接续 spawn（非用户点击）。
    # 不设 UNIQUE(job_id)：incremental job_id 稳定复用（inc-<channel>），重跑保留历史行。
    if "job" not in {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}:
        conn.execute(
            """CREATE TABLE job (
                 job_id TEXT NOT NULL,
                 kind TEXT NOT NULL,
                 channel TEXT NOT NULL,
                 status TEXT NOT NULL,            -- running | exited | failed | killed
                 pid INTEGER,
                 started_at REAL NOT NULL,
                 finished_at REAL,
                 exit_code INTEGER,
                 chained INTEGER NOT NULL DEFAULT 0,
                 log TEXT                          -- 日志文件相对路径（status 端点读尾部）
               )""")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_channel_started "
            "ON job(channel, started_at DESC)")
    else:
        # 旧 schema 列迁移：表可能由早期版本（无 log 列）建过——IF NOT EXISTS 不改旧表
        jcols = {r["name"] for r in conn.execute("PRAGMA table_info(job)")}
        if "log" not in jcols:
            conn.execute("ALTER TABLE job ADD COLUMN log TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_channel_started "
            "ON job(channel, started_at DESC)")


def insert_job(conn: sqlite3.Connection, *, job_id: str, kind: str, channel: str,
               pid: int, started_at: float, chained: bool = False,
               log: str | None = None) -> None:
    # ponytail: 老 DB job.job_id 是 PRIMARY KEY——incremental 重跑 inc-<channel>
    # 复用 job_id 会撞 UNIQUE 被静默吞掉，新 spawn 永远落不到表。
    # INSERT OR REPLACE（upsert）同一 job_id 旧行覆盖：reaper 的 finish_job
    # WHERE status='running' 才能正确收尾。
    conn.execute(
        "INSERT OR REPLACE INTO job(job_id, kind, channel, status, pid, started_at, "
        "chained, log) VALUES(?, ?, ?, 'running', ?, ?, ?, ?)",
        (job_id, kind, channel, pid, started_at, int(chained), log))
    conn.commit()


def finish_job(conn: sqlite3.Connection, job_id: str, status: str,
               exit_code: int | None) -> None:
    """收割：running → 终态（exited/failed/killed）+ finished_at（epoch 秒）。

    WHERE 带 status='running'：同 job_id 历史终态行（incremental 重跑）不受影响。
    """
    import time as _time
    conn.execute(
        "UPDATE job SET status=?, exit_code=?, finished_at=? "
        "WHERE job_id=? AND status='running'",
        (status, exit_code, _time.time(), job_id))
    conn.commit()


def reap_orphan_jobs(conn: sqlite3.Connection) -> int:
    """API 启动收割：表里 running 但进程已死（重启期间退出）的行标 killed。

    PID 复用误判风险接受（后果仅状态标签错）。返回收割行数。
    """
    import os as _os
    import time as _time
    n = 0
    for r in conn.execute("SELECT rowid, pid FROM job WHERE status='running'").fetchall():
        pid = r["pid"]
        try:
            _os.kill(pid, 0)          # 还活着（单实例铁律下=本实例的 job）
            continue
        except ProcessLookupError:
            pass                       # 进程不存在 → 已死
        except PermissionError:
            continue                   # 存在但非本用户 → 视为活着
        conn.execute(
            "UPDATE job SET status='killed', finished_at=? "
            "WHERE rowid=? AND status='running'",
            (_time.time(), r["rowid"]))
        n += 1
    conn.commit()
    return n


def list_jobs(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT job_id, kind, channel, status, pid, started_at, finished_at, "
        "exit_code, chained, log FROM job ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def seed_fail_streaks(conn: sqlite3.Connection, window: int = 3) -> dict[str, int]:
    """每 channel 最近 seed 任务的连续失败次数（health 冒头依据）。

    连续 window 次全失败 → streak≥window（前端横幅）；遇到一次成功 → 截断（更
    早的失败不再纳入 streak）。

    2026-10-01 修复：之前实现 `streaks[ch] = 0` 在循环里只重置计数器但不 break——
    实际"按时间倒序遍历"会把更早的成功当成"插在中间的截断"，但更早的失败又
    被加回去，导致 streak 永远 ≤ 最近成功之后的失败数。智能爬取拿到的 OSM
    streak 实际上是 0（被几小时前的旧成功重置），退避永远不生效。
    现在：见到成功只把"该 channel 标记为已截断"，不再重置；后续更早的失败
    被跳过；最终 streak = 最近一次成功之后的连续失败数（无上限）。
    """
    streaks: dict[str, int] = {}
    seen_exit: set[str] = set()      # 该 channel 已经见过成功 → 停止累加更早失败
    rows = conn.execute(
        "SELECT channel, status FROM job WHERE kind='seed' "
        "ORDER BY started_at DESC LIMIT 200").fetchall()
    for r in rows:
        ch, st = r["channel"], r["status"]
        if st == "running":
            continue
        if st == "exited":
            seen_exit.add(ch)         # 截断：更早的 failure 不再纳入此 channel
            # 不重置 streaks[ch]：因为这是按时间倒序遍历，"成功"在循环里出现
            # 位置比已计入的失败更早（更老的 job）；之前的失败是更新发生的，
            # 它们的计数才是真正的 streak
        elif ch not in seen_exit:
            streaks[ch] = streaks.get(ch, 0) + 1
    return {ch: n for ch, n in streaks.items() if n >= window}


def upsert_domain(conn, key: str, channel: str, seed_host: str | None,
                  developer_name: str | None = None) -> None:
    """新实体首次落库：channel/seed_host/developer_name 仅首次写（ON CONFLICT 不动）。

    developer_name 来自种子渠道（play 渠道 = 开发者主体名），是 P0 第 1 强信号
    （spec 3.5）——首跑时入表，后续重跑不覆盖（防止某次爬取命中的开发者名篡改种子值）。
    """
    conn.execute(
        """INSERT INTO domain(entity_key, channel, seed_host, developer_name, first_seen)
           VALUES(?, ?, ?, ?, ?)
           ON CONFLICT(entity_key) DO NOTHING""",
        (key, channel, seed_host, developer_name, now()),
    )


def update_contact_status(conn, entity: str, status: str, notes: str | None = None) -> bool:
    """更新销售跟进状态（new/contacted/in_conversation/won/lost）。

    返回 True 表示更新成功，False 表示实体不存在。`contacted_at` 自动戳。
    仅 `contact_status` 与 `contact_notes` 改变，其他字段不动。
    """
    cur = conn.execute(
        """UPDATE domain SET
             contact_status = ?,
             contact_notes = COALESCE(?, contact_notes),
             contacted_at = CASE WHEN ? IN ('contacted','in_conversation','won','lost')
                                  THEN ? ELSE contacted_at END
           WHERE entity_key = ?""",
        (status, notes, status, now(), entity),
    )
    return cur.rowcount > 0


def upsert_phone(conn, e164: str, country: str | None) -> None:
    # 2026-09-30 修复：phone 重复入库时 country 也更新——以前 INSERT OR IGNORE
    # 会让首次入库的 CN 永久覆盖后续 JP 号码的国家，导致 score market/market_group 偏错
    conn.execute(
        """INSERT INTO phone(e164, valid, country) VALUES(?, 1, ?)
           ON CONFLICT(e164) DO UPDATE SET country=COALESCE(excluded.country, phone.country)""",
        (e164, country),
    )


def upsert_sighting(conn, key: str, e164: str, url: str, layer: str) -> None:
    ts = now()
    conn.execute(
        """INSERT INTO sighting(entity_key, e164, url, layer, first_seen, last_seen)
           VALUES(?, ?, ?, ?, ?, ?)
           ON CONFLICT(entity_key, e164, url)
           DO UPDATE SET last_seen = excluded.last_seen""",
        (key, e164, url, layer, ts, ts),
    )


def forget(conn, entity: str) -> int:
    """GDPR 删除响应（报告 7.4：必须项不是可选项）：按实体删 domain + sighting；
    phone 仅在无任何 sighting 引用时作为孤儿清理（一号多挂不得误删他司证据）。"""
    conn.execute("DELETE FROM sighting WHERE entity_key=?", (entity,))
    n = conn.execute("DELETE FROM domain WHERE entity_key=?", (entity,)).rowcount
    conn.execute(
        "DELETE FROM phone WHERE e164 NOT IN (SELECT DISTINCT e164 FROM sighting)")
    conn.commit()
    return n


# ============================================================================
# 智能爬虫（Smart Crawler，2026-09-30）：每 channel ROI 跟踪 + 错误连击冷却
# ============================================================================

def record_crawl_outcome(conn, channel: str, *, hit: bool = False,
                          phones_found: int = 0, errored: bool = False) -> None:
    """记录一次爬取 outcome——供智能调度选最优 channel。

    hit: 本次爬取产出了新 entity（含 WA 号码）
    phones_found: 本次爬取发现的号码数（增量）
    errored: 本次出现 5xx/timeout/4xx（不计 robots 拒——正常）
    冷却逻辑：连续 5+ 次 errored → 5 分钟冷却；成功一次 → 重置 consecutive_errors。
    """
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    conn.execute(
        """INSERT INTO crawl_stats(channel, attempts, hits, phones, errors,
                                 last_crawl_at, last_hit_at, consecutive_errors,
                                 updated_at)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(channel) DO UPDATE SET
             attempts = attempts + excluded.attempts,
             hits = hits + excluded.hits,
             phones = phones + excluded.phones,
             errors = errors + excluded.errors,
             last_crawl_at = excluded.last_crawl_at,
             last_hit_at = COALESCE(excluded.last_hit_at, crawl_stats.last_hit_at),
             consecutive_errors = CASE WHEN excluded.errored = 0 THEN 0
                                       ELSE consecutive_errors + 1 END,
             backoff_until = CASE
               WHEN excluded.errored = 0 THEN NULL  -- 成功：清冷却
               WHEN consecutive_errors + 1 >= 5
                 THEN datetime(?, '+5 minutes')
               ELSE backoff_until  -- 未达阈值：保留原冷却
             END,
             updated_at = ?""",
        (channel,
         1 if not errored else 1,  # attempts 都 +1（出错也算尝试过）
         1 if hit else 0,
         phones_found,
         1 if errored else 0,
         now if not errored else None,  # last_crawl_at 失败时不更新（保留上次成功时间）
         now if hit else None,
         1 if errored else 0,
         now,
         now),  # 给 CASE 用（datetime 函数）
    )


def get_crawl_stats(conn, channel: str) -> dict | None:
    """读单 channel ROI 状态——智能调度核心数据（无记录返回 None）。"""
    row = conn.execute(
        "SELECT * FROM crawl_stats WHERE channel=?", (channel,)).fetchone()
    return dict(row) if row else None


def get_all_crawl_stats(conn) -> list[dict]:
    """读全部 channel ROI（smart 模式排序用）。"""
    return [dict(r) for r in conn.execute("SELECT * FROM crawl_stats ORDER BY channel")]


def clear_backoff(conn, channel: str) -> None:
    """手动清除冷却——admin 确认 channel 恢复后调用。"""
    conn.execute(
        "UPDATE crawl_stats SET backoff_until=NULL, consecutive_errors=0, updated_at=datetime('now') WHERE channel=?",
        (channel,),
    )
