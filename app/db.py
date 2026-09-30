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

    # E2 fix: 索引补充（按 v7 spec 高频查询路径）
    # 单条 ALTER 后即时建索引——>10k 行后无索引会让 /api/leads 按 market_group 扫表
    conn.execute("CREATE INDEX IF NOT EXISTS idx_domain_market_group ON domain(market_group)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_domain_score ON domain(score DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_domain_p0 ON domain(p0) WHERE p0 = 1")  # 部分索引，小


def upsert_domain(conn, key: str, channel: str, seed_host: str | None,
                  developer_name: str | None = None) -> None:
    """新实体首次落库：channel/seed_host/developer_name 仅在首次写（ON CONFLICT 不动）。

    developer_name 来自种子渠道（play 渠道 = 开发者主体名），是 P0 第 1 强信号
    （spec 3.5）——首跑时入表，后续重跑不覆盖（防止某次爬取命中的开发者名篡改种子值）。
    """
    conn.execute(
        """INSERT INTO domain(entity_key, channel, seed_host, developer_name, first_seen)
           VALUES(?, ?, ?, ?, ?)
           ON CONFLICT(entity_key) DO NOTHING""",
        (key, channel, seed_host, developer_name, now()),
    )


def upsert_phone(conn, e164: str, country: str | None) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO phone(e164, valid, country) VALUES(?, 1, ?)",
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
