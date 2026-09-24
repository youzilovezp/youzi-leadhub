"""item 管道：两层检测 → 三层归一 → 三表落库；spider 关闭时回填打分。

幂等：全部 upsert（ON CONFLICT）——同批重跑不产生重复 sighting（报告 9.1 冒烟项）。
跨进程状态零内存：flags/widget 逐 item OR-merge 进 domain 行，close_spider 的打分
完全由 DB 派生——JOBDIR 断点续跑后新进程不需要旧进程的内存态（D2 审计 #3）。
"""
from __future__ import annotations

import sqlite3
from urllib.parse import urlsplit

from youzi_bsp import db
from youzi_bsp.detect import detect
from youzi_bsp.normalize import entity_key, normalize_phone, normalize_url
from youzi_bsp.score import page_flags, score_domain


class WaStorePipeline:
    def __init__(self, db_path: str = "data/leads.db"):
        self.db_path = db_path
        self.conn: sqlite3.Connection = None  # type: ignore[assignment]  # open_spider 里连接

    @classmethod
    def from_crawler(cls, crawler):
        return cls(db_path=crawler.settings.get("BSP_DB", "data/leads.db"))

    def open_spider(self, spider):
        self.conn = db.connect(self.db_path)

    def process_item(self, item, spider):
        url = normalize_url(item["url"]) or item["url"]
        host = (urlsplit(url).hostname or "").lower()
        key = entity_key(host)  # 实体归属按最终 host（报告 3.4）
        db.upsert_domain(self.conn, key, item.get("channel", "sample"),
                         item.get("seed_host"))

        # 失败留痕（spider errback 产 error item）：仅限"从未成功爬到任何一页"的实体——
        # last_crawled 只在成功路径写，非 NULL 说明首页已成功（同 host 子页 404/超时/
        # robots 拒不能把部分成功的域降级成 error，否则 score 清零 + sighting 导出脏行）
        if item.get("error"):
            self.conn.execute(
                """UPDATE domain SET status='error'
                   WHERE entity_key=? AND status='pending' AND last_crawled IS NULL""",
                (key,))
            self.conn.commit()
            return item
        # 成功重爬历史 error 域（如上次超时本次成功）：复活为 pending 交 close_spider 重算
        self.conn.execute(
            "UPDATE domain SET status='pending' WHERE entity_key=? AND status='error'",
            (key,))

        # 打分信号逐 item 持久化（跨页 OR-merge 在 SQL 里完成，进程可随时死）
        fl = page_flags(item["html"])
        self.conn.execute(
            """UPDATE domain SET
                 lang = COALESCE(lang, ?),
                 icp = icp | ?,
                 hreflang_zh = hreflang_zh | ?,
                 title_zh = title_zh | ?,
                 gambling = gambling | ?,
                 last_crawled = ?
               WHERE entity_key = ?""",
            (fl["lang"], int(fl["icp"]), int(fl["hreflang_zh"]),
             int(fl["title_zh"]), int(fl["gambling"]), db.now(), key),
        )

        res = detect(item["html"])
        if res["widgets"]:
            cur = self.conn.execute("SELECT widget FROM domain WHERE entity_key=?",
                                    (key,)).fetchone()
            merged = ({w for w in (cur["widget"] or "").split(",") if w}
                      | set(res["widgets"]))
            self.conn.execute("UPDATE domain SET widget=? WHERE entity_key=?",
                              (",".join(sorted(merged)), key))
        if res["emails"]:
            cur = self.conn.execute("SELECT email FROM domain WHERE entity_key=?",
                                    (key,)).fetchone()
            merged = ({e for e in (cur["email"] or "").split(",") if e}
                      | set(res["emails"]))
            self.conn.execute("UPDATE domain SET email=? WHERE entity_key=?",
                              (",".join(sorted(merged)), key))
        for raw, layer in res["candidates"]:
            ph = normalize_phone(raw, imply_plus=(layer == "link"))
            if not ph:
                continue
            e164, country = ph
            db.upsert_phone(self.conn, e164, country)
            db.upsert_sighting(self.conn, key, e164, url, layer)
        self.conn.commit()
        return item

    def close_spider(self, spider):
        """打分完全由 DB 派生：pending/scored（含历史遗留 pending）统一重算，幂等；
        error 行不碰——失败留痕不能被收尾打分覆盖。"""
        countries: dict[str, list[str | None]] = {}
        for entity, country in self.conn.execute(
                """SELECT s.entity_key, p.country FROM sighting s
                   JOIN phone p ON p.e164 = s.e164"""):
            countries.setdefault(entity, []).append(country)
        # 先物化再写：SELECT 游标扫描期间 UPDATE 同表属未定义行为（可能跳行）
        for d in self.conn.execute(
                """SELECT entity_key, lang, icp, hreflang_zh, title_zh, gambling
                   FROM domain WHERE status IN ('pending', 'scored')"""
        ).fetchall():
            fl = {"lang": d["lang"], "icp": d["icp"],
                  "hreflang_zh": d["hreflang_zh"], "title_zh": d["title_zh"],
                  "gambling": d["gambling"]}
            sc = score_domain(fl, countries.get(d["entity_key"], []), d["entity_key"])
            self.conn.execute(
                """UPDATE domain SET status='scored', p0=?, market=?, score=?
                   WHERE entity_key=?""",
                (sc["p0"], sc["market"], sc["score"], d["entity_key"]),
            )
        self.conn.commit()
        self.conn.close()
