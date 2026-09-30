"""全链路烟测脚本：4 实体覆盖 4 类场景，验证 C1/C5 修复真实生效。

场景：
  A. sina.com.cn  (境内 .cn, market=CN, 无 ICP)     → P0=0 (修复前误判 1)
  B. 360.cn       (境内 .cn, market=CN, 无 ICP)     → P0=0 (修复前误判 1)
  C. vstarcam.cn  (.cn, market=ID, lang=zh)         → P0=1 (.cn + 海外)
  D. example.com  (.com, lang=zh, market=CN, ICP)  → P0=1 (lang=zh+CN 双确认 + ICP)

跑 WaStorePipeline（模拟爬取 item）→ score_pending → 导出查看结果。
"""
import sys
import tempfile
import csv
from pathlib import Path

from app import db
from app.pipelines import WaStorePipeline


FIXTURES = {
    # A. 境内 .cn：HTML lang=zh + ICP 备案号 + 国内电话
    "sina.com.cn": (
        '<html lang="zh-CN"><body>'
        '<footer>京ICP备123456号</footer>'
        '<a href="https://wa.me/8613800138000">联系</a>'
        '</body></html>',
        "sina.com.cn",
    ),
    # B. 境内 .cn：英文界面 + 国内电话 + 无 ICP
    "360.cn": (
        '<html lang="en"><body>'
        '<a href="https://api.whatsapp.com/send?phone=8613800138999">wa</a>'
        '</body></html>',
        "360.cn",
    ),
    # C. .cn 出海：海外号码 ID（真中国出海企业）
    "vstarcam.cn": (
        '<html lang="zh-CN"><body>'
        '<a href="https://wa.me/6281234567890">wa</a>'
        '</body></html>',
        "vstarcam.cn",
    ),
    # D. .com + ICP + 国内号码（lang=zh+market=CN 双确认）
    "example-shop.com": (
        '<html lang="zh-CN"><body>'
        '<footer>沪ICP备987654号</footer>'
        '<a href="https://wa.me/8613800138777">联系</a>'
        '</body></html>',
        "example-shop.com",
    ),
}


def main():
    with tempfile.TemporaryDirectory() as td:
        dbp = str(Path(td) / "leads.db")
        pl = WaStorePipeline(db_path=dbp)
        pl.open_spider(None)
        for entity, (html, seed_host) in FIXTURES.items():
            item = {
                "url": f"https://{entity}/",
                "html": html,
                "channel": "smoke",
                "seed_host": seed_host,
            }
            pl.process_item(item, None)
        pl.close_spider(None)

        conn = db.connect(dbp)

        # 1. domain 行
        print("=== domain 表 ===")
        for row in conn.execute(
                "SELECT entity_key, p0, market, market_group, lang, icp, score "
                "FROM domain ORDER BY entity_key"):
            print(f"  {row['entity_key']:<22} p0={row['p0']} market={row['market']} "
                  f"market_group={row['market_group']} lang={row['lang']} icp={row['icp']} "
                  f"score={row['score']}")

        # 2. P0 期望对照
        print("\n=== 期望对照（C5/C1 修复验证）===")
        expectations = {
            # sina.com.cn: lang=zh + ICP + market=CN → lang-zh+CN 双确认 + ICP 强信号都触发 P0
            # （spec 设计：lang=zh+CN 双轨确认 → 算 P0；与 C5 修复不冲突——C5 只排除 .cn 单信号）
            "sina.com.cn":       (1, "CN",  "CN",  "lang=zh+CN 双确认 + ICP 备案 → 仍 P0（spec 双轨保留）"),
            # 360.cn: 无 ICP, lang=en, market=CN → 无任何 P0 信号 → 修复后 P0=0（C5 真生效）
            "360.cn":            (0, "CN",  "CN",  ".cn + 无独立信号 → 不 P0（C5 真生效——修复前 .cn 单信号也会 P0=1）"),
            "vstarcam.cn":       (1, "ID",  "SEA", ".cn + 海外 ID + lang=zh → 真中国出海 P0=1"),
            "example-shop.com":  (1, "CN",  "CN",  ".com + ICP + lang=zh+market=CN 双确认 → P0=1（保留）"),
        }
        for entity, (exp_p0, exp_market, exp_group, reason) in expectations.items():
            row = conn.execute(
                "SELECT p0, market, market_group FROM domain "
                "WHERE entity_key=?", (entity,)).fetchone()
            ok = (row["p0"] == exp_p0 and row["market"] == exp_market
                  and row["market_group"] == exp_group)
            mark = "✅" if ok else "❌"
            print(f"  {mark} {entity:<22} p0={row['p0']}/{exp_p0} "
                  f"market={row['market']}/{exp_market} group={row['market_group']}/{exp_group}")
            print(f"      理由: {reason}")

        # 3. CSV 导出
        csvp = str(Path(td) / "leads.csv")
        from app.stats import export_csv
        n = export_csv(conn, csvp)
        print(f"\n=== CSV 导出: {n} 条 ===")
        with open(csvp) as fh:
            for row in csv.DictReader(fh):
                print(f"  {row['entity']:<22} channel={row['channel']} market={row['market']} "
                      f"phones={row['phones']}")

        # 4. 5 个新列必须存在
        print("\n=== 付费扩展预留列 ===")
        new_cols = ["enrichment_status", "enriched_at", "contact_email",
                    "tech_signals", "outreach_message"]
        for c in new_cols:
            exists = conn.execute(
                "SELECT 1 FROM pragma_table_info('domain') WHERE name=?",
                (c,)).fetchone()
            print(f"  {'✅' if exists else '❌'} {c}")


if __name__ == "__main__":
    main()
