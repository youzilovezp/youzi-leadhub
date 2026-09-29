"""CLI：python -m youzi_bsp seed|crawl|stats|export。"""
from __future__ import annotations

import argparse
from pathlib import Path

from youzi_bsp import db, seeds, stats


def _max_pages(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError(
            f"--max-pages 必须 ≥ 1（实测 0/负数会让预算计算退化），got {n}")
    return n


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="youzi_bsp",
                                description="WhatsApp BSP 线索获取管道（只发现不外联）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed", help="生成种子文件到 data/")
    s.add_argument("channel", choices=["tranco", "myshopify", "play", "osm", "sample"])
    s.add_argument("--top", type=int, default=2000, help="tranco: top N")
    s.add_argument("--limit", type=int, default=2000, help="myshopify/play/osm: 数量上限")
    s.add_argument("--country", default="id",
                   help="play/osm: 国家码逗号分隔（play: id/br/mx；osm: MY,TH,PH）")
    s.add_argument("--category", default="BUSINESS",
                   help="play: 类目（BUSINESS/SHOPPING/COMMUNICATION）")
    s.add_argument("--crawl-id", default=None, help="myshopify: CC 档期 id，默认最新")
    s.add_argument("--file", default=None, help="sample: URL/域名清单文件")
    s.add_argument("--out", default=None)

    c = sub.add_parser("crawl", help="跑爬虫（Scrapy）")
    c.add_argument("--seed-file", required=True)
    c.add_argument("--channel", default="sample")
    c.add_argument("--limit", type=int, default=1000)
    c.add_argument("--max-pages", type=_max_pages, default=5)
    c.add_argument("--delay", type=float, default=0.0,
                   help="每域下载间隔秒（被 429 限流的渠道用它降速，如 myshopify 用 2~3）")
    c.add_argument("--concurrency", type=int, default=0,
                   help="全局并发上限（IP 级 429 限流时压低，如 myshopify 用 6）")
    c.add_argument("--db", default="data/leads.db")
    c.add_argument("--jobdir", default=None,
                   help="断点续跑目录（Scrapy JOBDIR：队列/去重指纹持久化）；"
                        "缺省时落到 data/.job/<channel>-<yyyymmdd>/ 避免忘传丢续跑")

    st = sub.add_parser("stats", help="分渠道命中率")
    st.add_argument("--db", default="data/leads.db")

    e = sub.add_parser("export", help="导出线索 CSV")
    e.add_argument("--db", default="data/leads.db")
    e.add_argument("--out", default="data/leads.csv")

    fg = sub.add_parser("forget", help="GDPR 删除：按实体删线索与证据")
    fg.add_argument("--db", default="data/leads.db")
    fg.add_argument("--entity", required=True, help="实体键（eTLD+1，如 example.com）")

    imp = sub.add_parser("import-osm",
                         help="OSM 直标签导入：phone/contact:whatsapp 免爬取直接入库")
    imp.add_argument("--country", default="MY", help="ISO 国家码逗号分隔（MY,TH,PH）")
    imp.add_argument("--limit", type=int, default=1000)
    imp.add_argument("--db", default="data/leads.db")

    gp = sub.add_parser("golden-prelabel",
                        help="金标准集预标注：抓样本页缓存 HTML + 检测器重放，"
                             "label 预填建议值，人工只纠错（报告 9.1）")
    gp.add_argument("--csv", default="docs/golden-set-sample.csv")
    gp.add_argument("--cache", default="data/golden-html")

    ge = sub.add_parser("golden-eval",
                        help="金标准集 precision 重放裁决（报告 9.2 阈值："
                             "链接层 P≥98%%、文本层 FP≤5%%）")
    ge.add_argument("--csv", default="docs/golden-set-sample.csv")

    a = p.parse_args(argv)
    data = Path("data")

    if a.cmd == "seed":
        out = Path(a.out) if a.out else data / f"seeds-{a.channel}.txt"
        if a.channel == "tranco":
            seeds.tranco(a.top, out)
        elif a.channel == "myshopify":
            seeds.myshopify(a.limit, out, a.crawl_id)
        elif a.channel == "play":
            seeds.play(a.country, a.category, a.limit, out)
        elif a.channel == "osm":
            seeds.osm(a.country, a.limit, out)
        else:
            if not a.file:
                p.error("sample 渠道需要 --file")
            seeds.sample(a.file, out)
        print(f"seed 文件已生成: {out}")

    elif a.cmd == "crawl":
        import os
        from datetime import datetime

        os.environ.setdefault("SCRAPY_SETTINGS_MODULE", "youzi_bsp.settings")
        from scrapy.crawler import CrawlerProcess
        from scrapy.utils.project import get_project_settings

        sset = get_project_settings()
        sset.set("BSP_DB", a.db)
        # MED 修复：--jobdir 缺省时落到 data/.job/<channel>-<yyyymm-dd>/，避免
        # 5 万站中途崩溃从头爬。显式传 --jobdir "" 仍生效（空字符串 = 不续跑）。
        if a.jobdir is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            default_jobdir = Path("data") / ".job" / f"{a.channel}-{stamp}"
            default_jobdir.mkdir(parents=True, exist_ok=True)
            a.jobdir = str(default_jobdir)
        sset.set("JOBDIR", a.jobdir)
        if a.delay:
            sset.set("DOWNLOAD_DELAY", a.delay)
        if a.concurrency:
            sset.set("CONCURRENT_REQUESTS", a.concurrency)
            sset.set("AUTOTHROTTLE_TARGET_CONCURRENCY", float(a.concurrency))
        process = CrawlerProcess(sset)
        process.crawl("wa", seed_file=a.seed_file, channel=a.channel,
                      limit=a.limit, max_pages=a.max_pages)
        process.start()

    elif a.cmd == "stats":
        conn = db.connect(a.db)
        for row in stats.channel_stats(conn):
            print(f"{row['channel']:<12} total={row['total']:<6} hits={row['hits']:<5} "
                  f"p0={row['p0']:<4} hit_rate={row['hit_rate']:.1%}")

    elif a.cmd == "export":
        conn = db.connect(a.db)
        n = stats.export_csv(conn, a.out)
        print(f"CSV 已导出: {a.out}（{n} 条带号码线索）")

    elif a.cmd == "forget":
        conn = db.connect(a.db)
        n = db.forget(conn, a.entity)
        print(f"已删除 {n} 个实体及其证据" if n else f"未找到实体: {a.entity}")

    elif a.cmd == "import-osm":
        from youzi_bsp.osm_direct import import_osm
        n = import_osm(a.country, a.limit, a.db)
        print(f"OSM 直标签导入: {n} 条线索（幂等，可重复执行）")

    elif a.cmd == "golden-prelabel":
        from youzi_bsp import golden
        st = golden.prelabel(Path(a.csv), Path(a.cache))
        print(f"预标注完成: 抓到 {st['fetched']} / 失败 {st['missed']} / "
              f"重放复现 {st['reproduced']}——label 列已预填建议值，"
              f"人工只需纠错改 FP")

    elif a.cmd == "golden-eval":
        from youzi_bsp import golden
        print(golden.report(golden.evaluate(Path(a.csv))))


if __name__ == "__main__":
    main()
