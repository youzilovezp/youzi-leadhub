"""种子渠道（报告 3.2）→ 种子文件（每行一个 URL）。

- tranco：Tranco top-N（免费、每日更新）——长尾补充
- myshopify：CDX Index Server 免费通配查询（host 聚合、分页拉取；继承 CC 头部偏置）
- play：google-play-scraper 按国家×类目分榜（必须限定类目，总榜被大 app 霸榜）
- sample：手工清单（金标准集/测试用）
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

TRANCO_URL = "https://tranco-list.eu/top-1m.csv.zip"
COLLINFO = "https://index.commoncrawl.org/collinfo.json"


def _write(out: Path, urls: list[str]) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(urls) + "\n", encoding="utf-8")
    return out


def tranco(top: int, out: Path) -> Path:
    r = httpx.get(TRANCO_URL, follow_redirects=True, timeout=120)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        lines = zf.read(zf.namelist()[0]).decode().splitlines()
    domains = [ln.split(",")[1] if "," in ln else ln for ln in lines[:top]]
    return _write(out, [f"https://{d.strip()}/" for d in domains if d.strip()])


def myshopify(limit: int, out: Path, crawl_id: str | None = None) -> Path:
    with httpx.Client(timeout=60, headers={"User-Agent": "youzi-bsp-leadgen/0.1"}) as client:
        if not crawl_id:
            # 最新档索引常未就绪（502）——从 collinfo 逐个回退到可用档
            colls = client.get(COLLINFO).json()
            ids = [c["id"] for c in colls[:6]]
        else:
            ids = [crawl_id]
        for _id in ids:
            probe = client.get(
                f"https://index.commoncrawl.org/{_id}-index"
                f"?url={quote('*.myshopify.com/*')}&output=json&limit=1"
            )
            if probe.status_code == 200:
                crawl_id = _id
                break
        else:
            raise RuntimeError(f"CC CDX 全部档期不可用: {ids}")
        base = (f"https://index.commoncrawl.org/{crawl_id}-index"
                f"?url={quote('*.myshopify.com/*')}&output=json")
        hosts: set[str] = set()
        resume: str | None = None
        # ponytail: 最多 50 页分页——种子够 D1-3 用；要全量再调
        for _ in range(50):
            r = client.get(f"{base}&resumeKey={resume}" if resume else base)
            r.raise_for_status()
            nxt: str | None = None
            for ln in r.text.splitlines():
                if not ln.strip():
                    continue
                try:
                    rec = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if "url" in rec:
                    h = urlsplit(rec["url"]).hostname or ""
                    if h.endswith(".myshopify.com"):
                        hosts.add(h)
                if rec.get("resumeKey"):
                    nxt = rec["resumeKey"]
            if nxt == resume or len(hosts) >= limit:
                break
            resume = nxt
    return _write(out, [f"https://{h}/" for h in sorted(hosts)[:limit]])


def play(country: str, category: str, limit: int, out: Path) -> Path:
    """App 渠道：国家×类目分榜 → 开发者官网。

    分榜 API 仅 JS 版 google-play-scraper 有（Python 版只剩 search/app/reviews），
    且官网字段名是 developerWebsite——统一走 scripts/play-seeds.mjs。
    country/category 支持逗号分隔多值（id,br,mx / BUSINESS,SHOPPING）。
    CN 网络需先 export NODE_USE_ENV_PROXY=1 HTTPS_PROXY=http://127.0.0.1:7890。
    依赖：cd scripts && npm i（见 scripts/package.json）。
    """
    import shutil
    import subprocess

    if not shutil.which("node"):
        raise RuntimeError("play 渠道需要 node 运行 scripts/play-seeds.mjs")
    script = Path(__file__).resolve().parent.parent / "scripts" / "play-seeds.mjs"
    if not script.exists():
        raise RuntimeError(f"缺少种子脚本: {script}")
    subprocess.run(
        ["node", str(script), "--countries", country, "--categories", category,
         "--num", str(limit), "--out", str(out)],
        check=True,
    )
    return out


def sample(src: str, out: Path) -> Path:
    """手工清单（域名或 URL 混合）→ 规范化种子文件。"""
    from youzi_bsp.normalize import normalize_url

    urls = []
    for line in Path(src).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        u = normalize_url(line if "://" in line else f"https://{line}")
        if u:
            urls.append(u)
    return _write(out, urls)


# Overpass 公共端点（免费、限速；CN 网络如被拒走 HTTPS_PROXY）
_OVERPASS = "https://overpass-api.de/api/interpreter"


def osm(countries: str, limit: int, out: Path) -> Path:
    """OSM Overpass 渠道：本地商家（shop/餐饮/咖啡馆）带 website 标签 → 种子。

    leadhub 遗产复活：曾产出 1208 线索 / 72 条 wa.me 的被砍通道（leadhub 诊断
    R1——唯一规模验证过的路线）。OSM 商家天然是"本地 SMB 挂 WA 迎客"人群。
    countries: ISO3166-1 代码逗号分隔（MY,TH,PH,ID…）；limit 为总目标数（按国均摊）。
    """
    codes = [c.strip().upper() for c in countries.split(",") if c.strip()]
    per = max(limit // max(len(codes), 1), 50)
    urls: list[str] = []
    for code in codes:
        # ["website"] 标签存在性过滤：只取有官网的商家（实测无过滤时仅 ~18% 带 website）
        q = (f'[out:json][timeout:180];area["ISO3166-1"="{code}"]->.a;'
             f'(nwr["shop"]["website"](area.a);'
             f'nwr["amenity"~"^(restaurant|cafe|fast_food)$"]["website"](area.a););'
             f'out tags {per};')
        r = httpx.post(_OVERPASS, data={"data": q}, timeout=180,
                       headers={"User-Agent": "youzi-leadhub/0.1 "
                                              "(BSP leadgen; polite; youzi99013@gmail.com)"})
        r.raise_for_status()
        for el in r.json().get("elements", []):
            w = (el.get("tags", {}).get("website") or "").strip()
            if w.startswith("http"):
                urls.append(w)
    return _write(out, urls)
