"""渠道命中率统计 + 线索 CSV 导出（报告 八：D1–3 核心产出）。"""
from __future__ import annotations

import csv


def channel_stats(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT d.channel,
               COUNT(*)                                        AS total,
               SUM(CASE WHEN EXISTS(SELECT 1 FROM sighting s
                                     WHERE s.entity_key = d.entity_key)
                        THEN 1 ELSE 0 END)                     AS hits,
               SUM(d.p0)                                       AS p0
        FROM domain d
        WHERE d.status IN ('scored', 'error', 'pending')
        GROUP BY d.channel
        ORDER BY d.channel
        """
    ).fetchall()
    return [dict(r, hit_rate=round((r["hits"] or 0) / r["total"], 4)) for r in rows]


def export_csv(conn, out_path: str) -> int:
    rows = conn.execute(
        """
        SELECT d.entity_key, d.channel, d.market, d.market_group, d.lang, d.p0,
               d.score, d.developer_name, d.widget, d.email,
               d.enrichment_status, d.tech_signals,
               GROUP_CONCAT(DISTINCT s.e164) AS phones
        FROM domain d
        JOIN sighting s ON s.entity_key = d.entity_key
        GROUP BY d.entity_key
        ORDER BY d.score DESC, d.entity_key
        """
    ).fetchall()
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["entity", "channel", "market", "market_group", "lang", "p0",
                    "score", "developer_name", "widget", "email",
                    "enrichment_status", "tech_signals", "phones"])
        for r in rows:
            w.writerow([r["entity_key"], r["channel"], r["market"], r["market_group"],
                        r["lang"], r["p0"], r["score"], r["developer_name"],
                        r["widget"], r["email"], r["enrichment_status"],
                        r["tech_signals"], r["phones"]])
    return len(rows)
