"""item 管道：两层检测 → 三层归一 → 三表落库；spider 关闭时回填打分。

幂等：全部 upsert（ON CONFLICT）——同批重跑不产生重复 sighting（报告 9.1 冒烟项）。
跨进程状态零内存：flags/widget 逐 item OR-merge 进 domain 行，close_spider 的打分
完全由 DB 派生——JOBDIR 断点续跑后新进程不需要旧进程的内存态（D2 审计 #3）。

widget/email 的 OR-merge 用 SQLite 原子 SQL（HIGH #9 修复）：旧 read-modify-
write 在多进程并发下会丢元素（D6+ 分级重访触发）。
"""
from __future__ import annotations

import json
import sqlite3
from urllib.parse import urlsplit

from youzi_bsp import db
from youzi_bsp.detect import detect
from youzi_bsp.normalize import entity_key, normalize_phone, normalize_url
from youzi_bsp.score import page_flags, score_domain


def _atomic_merge_csv(conn, table: str, key: str, new_values: set[str],
                       column: str) -> None:
    """SQLite 原子 OR-merge：json_each 展开新旧 CSV 集合去重后 GROUP_CONCAT 写回。

    E1 fix: 单个值超 4KB 截断——widget 名（如 joinchat_settings 含大段 JS 字符串），
    或 attacker 灌入超长字符串，会让 domain 行变 MB 级。截到 4KB 保留信号同时防 bloat。

    2026-09-30 P0 修复：拒收 ASCII 控制字符（任何 ord(c) < 0x20）。原过滤器只挡
    ','/'"'/'\\'，但 mailto 链接含 URL 编码控制字符（典型：mailto:%00null@x.com
    经 unquote 变 'foo\x00null@x.com'）能进入。流程：①detect 正则 `\\s` 不挡 \x00
    → ②本函数过 padding → ③第一次合并：json.dumps 把 \x00 转义成 \\u0000，新值正常
    写入 → ④**第二次合并**：SQL 直接把旧值 'foo\x00null@x.com' wrap 进 JSON 字符串，
    json_each 抛 malformed JSON → Scrapy 丢整个 item（candidates/widgets 全废）。
    真实证据：data/.crawl-logs/_inc-osm.log 2026-09-28 21:31:25~18:47:40 出现 36 次
    `sqlite3.OperationalError: malformed JSON`——首页 widget/email 落库后，同站第 2 个
    item（联系页）踩中 → 联系页号码系统性丢失。修复即在配置阶段直接丢弃任何含控制
    字符的 token；JSON 中 raw control char 必转义，而 SQL CASE 表达式不转义旧值。
    """
    if not new_values:
        return
    # 单 token 限长：widget 名通常是 1-30 字符（joinchat, tidio, wa_business...），
    # 超过 4KB 视为异常输入直接截断
    MAX_TOKEN = 4096
    # ponytail: 控制字符判定走 ord < 0x20（JSON spec: control chars in string
    # must be \uXXXX escaped）；DEL(\x7f) 也拒——URL normalize 已拒，但 detect 阶段
    # mailto 解码可能漏，留兜底。实现为生成器+集合推导一行，无额外依赖。
    new_values = {v[:MAX_TOKEN] for v in new_values
                  if v and "," not in v and '"' not in v and "\\" not in v
                  and not any(ord(c) < 0x20 for c in v)}
    if not new_values:
        return
    new_json = json.dumps(sorted(new_values))
    conn.execute(
        f"""UPDATE {table}
            SET {column} = (
              SELECT GROUP_CONCAT(v, ',')
              FROM (
                SELECT DISTINCT value AS v FROM json_each(
                  CASE WHEN {column} IS NULL OR {column} = ''
                       THEN '[]'
                       ELSE '["' || REPLACE({column}, ',', '","') || '"]'
                  END
                )
                UNION
                SELECT value AS v FROM json_each(?)
              )
            )
            WHERE entity_key = ?""",
        (new_json, key),
    )


def score_pending(conn: sqlite3.Connection) -> None:
    """DB 派生打分：pending/scored（含历史遗留 pending）统一重算，幂等。

    close_spider 与 import-osm 共用（2026-09-28 P1 修复）：导入渠道不再自行算分
    ——旧路径用 {"lang": None} 空 flags 直接 UPDATE，会把已爬取域持久化的
    icp/hreflang_zh/developer_name 派生结果（p0=1）覆盖归零，且漏写 market_group。
    """
    countries: dict[str, list[str | None]] = {}
    for entity, country in conn.execute(
            """SELECT s.entity_key, p.country FROM sighting s
               JOIN phone p ON p.e164 = s.e164"""):
        countries.setdefault(entity, []).append(country)
    # 先物化再写：SELECT 游标扫描期间 UPDATE 同表属未定义行为（可能跳行）
    for d in conn.execute(
            """SELECT entity_key, lang, icp, hreflang_zh, title_zh, gambling,
                      widget, developer_name
               FROM domain WHERE status IN ('pending', 'scored')"""
    ).fetchall():
        widget = d["widget"] or ""
        fl = {"lang": d["lang"], "icp": d["icp"],
              "hreflang_zh": d["hreflang_zh"], "title_zh": d["title_zh"],
              "gambling": d["gambling"],
              "wa_group": "wa_group" in widget,       # 私域社群运营（M2-3）
              "wa_business": "wa_business" in widget}
        sc = score_domain(fl, countries.get(d["entity_key"], []),
                          d["entity_key"],
                          developer_name=d["developer_name"] or "")
        conn.execute(
            """UPDATE domain SET status='scored', p0=?, market=?, market_group=?,
                                 score=?
               WHERE entity_key=?""",
            (sc["p0"], sc["market"], sc["market_group"], sc["score"],
             d["entity_key"]),
        )


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
        url = normalize_url(item["url"])
        # 空 / 畸形 URL 直接跳过——entity_key("") 会写入空键污染 DB（G1/G2 bug）
        if not url:
            return item
        host = (urlsplit(url).hostname or "").lower()
        key = entity_key(host)
        if not key:                               # 主机名解析失败也跳过
            return item
        # developer_name 来自种子渠道（play = 开发者主体名；spec 3.5 P0 第 1 强信号）
        dev_name = (item.get("developer_name")
                     or (spider._dev_names.get(url, "") if spider else "")
                     or "").strip() or None
        db.upsert_domain(self.conn, key, item.get("channel", "sample"),
                         item.get("seed_host"), dev_name)

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
        # HIGH #9 修复：widget/email OR-merge 走 SQLite 原子 SQL（多进程并发安全）
        if res["widgets"]:
            _atomic_merge_csv(self.conn, "domain", key, set(res["widgets"]), "widget")
        if res["emails"]:
            _atomic_merge_csv(self.conn, "domain", key, set(res["emails"]), "email")
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
        """打分完全由 DB 派生（score_pending）：幂等；error 行不碰——失败留痕
        不能被收尾打分覆盖。"""
        score_pending(self.conn)
        self.conn.commit()
        self.conn.close()
