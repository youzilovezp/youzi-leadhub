"""种子渠道（报告 3.2）→ 种子文件（每行一个 URL）。

- tranco：Tranco top-N（免费、每日更新）——长尾补充
- myshopify：CDX Index Server 免费通配查询（host 聚合、分页拉取；继承 CC 头部偏置）
- play：google-play-scraper 按国家×类目分榜（必须限定类目，总榜被大 app 霸榜）
- sample：手工清单（金标准集/测试用）
"""
from __future__ import annotations

import io
import json
import os
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


def append_merge(target: Path, src: Path) -> int:
    """把 src 种子行追加进 target，按 URL（行首 \t 前部分）去重。

    增量挖新人群的落点：换国家×类目生成的新种子与旧池合并，增量爬取只吃
    没见过的 URL——旧行原样保留（play 渠道的 URL<TAB>developerName 不丢）。
    返回追加的新行数。"""
    def urls(path: Path) -> list[str]:
        return [ln for ln in path.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]

    old = urls(target) if target.exists() else []
    seen = {ln.split("\t", 1)[0].strip() for ln in old}
    fresh = [ln for ln in urls(src) if ln.split("\t", 1)[0].strip() not in seen]
    if fresh:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("\n".join(fresh) + "\n")
    return len(fresh)


def tranco(top: int, out: Path) -> Path:
    r = httpx.get(TRANCO_URL, follow_redirects=True, timeout=120)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        lines = zf.read(zf.namelist()[0]).decode().splitlines()
    domains = [ln.split(",")[1] if "," in ln else ln for ln in lines[:top]]
    return _write(out, [f"https://{d.strip()}/" for d in domains if d.strip()])


def myshopify(limit: int, out: Path, crawl_id: str | None = None) -> Path:
    # 2026-10-01：Shopify 默认给每个新账号随机分配子域 `xxx-nn.myshopify.com`，
    # CDX `*.myshopify.com/*` 拉回来 86% 是这种 random hash 子域（实测 837 条种子里
    # 723 条匹配），绝大多数是 test/abandoned 店 → myshopify 命中率 0.57%（README 也
    # 标记该渠道 0.7% / 55% 死站）。过滤保留"像真名"的子域：至少含一段 ≥4 字母
    # （真人店常用 brand/product 名，多音节），或纯 5+ 字母不带 dash（短品牌名）。
    import re as _re_myshopify
    import time as _time_myshopify
    # 2026-10-01：判定标准从"匹配 random 模式"改为"非 real 模式即过滤"。
    # 之前 `_RANDOM_HASH_RE.match()` 只识别 X-N 短 dash 模式，无法识别多 dash 的
    # random hash（如 `02-0457aa-mol`、`1-10-rod-shop`），导致 86% 仍过线。
    # 现在反过来：除非含 ≥6 字母真词（"airsoft"/"workshop"/"fashion"）或短纯字母，
    # 一律视为 random hash 过滤掉。
    _REAL_LOOKING_RE = _re_myshopify.compile(r"[a-z]{6,}")

    def _looks_like_real_shop(sub: str) -> bool:
        # 短名（≤5 纯字母如 "shop"）也算——人取短名通常纯字母
        if len(sub) <= 5 and "-" not in sub and sub.isalpha():
            return True
        # 含 ≥6 个连续字母（"airsoft" 7、"fashion" 7 真词；"avzhh" 5 仍属 random hash）
        return bool(_REAL_LOOKING_RE.search(sub))

    with httpx.Client(timeout=60, headers={"User-Agent": "youzi-bsp-leadgen/0.1"}) as client:
        if not crawl_id:
            # 最新档索引常未就绪（502）——从 collinfo 逐个回退到可用档
            colls = client.get(COLLINFO).json()
            ids = [c["id"] for c in colls[:6]]
        else:
            ids = [crawl_id]
        for _id in ids:
            # 2026-10-01：probe 重试 3 次（CDX 502/504 间歇性，命中立即 break）
            for _probe in range(3):
                probe = client.get(
                    f"https://index.commoncrawl.org/{_id}-index"
                    f"?url={quote('*.myshopify.com/*')}&output=json&limit=1"
                )
                if probe.status_code == 200:
                    crawl_id = _id
                    break
                _time_myshopify.sleep(2)
            if crawl_id:
                break
        if not crawl_id:
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
                        sub = h.removesuffix(".myshopify.com")
                        # 2026-10-01：过滤掉非真名子域——random hash 占 myshopify
                        # 子域 86%，绝大多数是 test/abandoned 店，命中率 0.57%。
                        # 保留：含 ≥6 真词字母 / 短纯字母（≤5 字符）。
                        if not _looks_like_real_shop(sub):
                            continue
                        hosts.add(h)
                if rec.get("resumeKey"):
                    nxt = rec["resumeKey"]
            # 末页响应无 resumeKey（nxt=None）必须终止——旧条件 nxt==resume 判不上
            # None，会乒乓回拉第 1 页直到 50 次上限（~48 次冗余请求白打免费端点）
            if nxt is None or nxt == resume or len(hosts) >= limit:
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
    try:
        subprocess.run(
            ["node", str(script), "--countries", country, "--categories", category,
             "--num", str(limit), "--out", str(out)],
            check=True,
            timeout=600,  # 2026-09-30 修复：硬上限 10 分钟——node 卡死时
                          # 后台 seed 任务永不结束，_JOBS_RUNNING 累积
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"play 渠道超时（>10min）——node 网络卡死: {e}")
    return out


def sample(src: str, out: Path) -> Path:
    """手工清单（域名或 URL 混合）→ 规范化种子文件。"""
    from app.normalize import normalize_url

    urls = []
    for line in Path(src).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        u = normalize_url(line if "://" in line else f"https://{line}")
        if u:
            urls.append(u)
    return _write(out, urls)


# App Store 类目 ID（legacy iTunes RSS genre 参数；实测 6000=Business、6024=Shopping）
# token 与 play 类目同名（场景 plans 复用同一套 token，映射不到的跳过）
_ITUNES_GENRE = {
    "BUSINESS": "6000",
    "SHOPPING": "6024",
    "FINANCE": "6015",
    "FOOD_AND_DRINK": "6023",
    "PRODUCTIVITY": "6007",
    "COMMUNICATION": "6005",
    "LIFESTYLE": "6012",
    "TRAVEL": "6009",
}


def itunes(countries: str, categories: str, limit: int, out: Path) -> Path:
    """iOS App Store 渠道（2026-10-01 燃料救火主力）：

    legacy iTunes RSS top-free 榜单（国家×类目，官方免费无 key）→ app id →
    Lookup API sellerUrl（开发者自填官网）+ sellerName（法律主体名，P0 第 1 强
    信号——与 play 的 developerName 同语义）。种子行与 play 同格式
    URL<TAB>主体名，spider 无需改动。与 play 互补：独享 iOS 商家池、无 Node
    依赖（纯 httpx）。sellerUrl 未填的 app 跳过；host 级去重（一开发者多 app
    只进池一次——实测 GoPay/Tokopedia 同主体多 app 刷榜）。
    """
    import time
    from app.normalize import entity_key

    codes = [c.strip().lower() for c in countries.split(",") if c.strip()]
    genre_ids = [g for g in (_ITUNES_GENRE.get(t.strip().upper())
                             for t in categories.split(",")) if g]
    proxy = _cn_proxy()
    headers = {"User-Agent": _UA}
    per = max(min(limit // max(len(codes), 1), 200), 10)
    lines: list[str] = []
    seen_hosts: set[str] = set()
    for cc in codes:
        ids: list[str] = []
        for gid in genre_ids or [None]:
            base = f"https://itunes.apple.com/{cc}/rss/topfreeapplications/limit={per}"
            url = f"{base}/genre={gid}/json" if gid else f"{base}/json"
            r = httpx.get(url, timeout=30, headers=headers, proxy=proxy,
                          follow_redirects=True)
            r.raise_for_status()
            entries = r.json().get("feed", {}).get("entry") or []
            if isinstance(entries, dict):    # 单条结果 Apple 返回对象非数组
                entries = [entries]
            ids.extend(e["id"]["attributes"]["im:id"] for e in entries)
            time.sleep(1)                    # 免费端点礼貌间隔
        for i in range(0, len(ids), 100):    # Lookup 批量 ≤100 ids/请求
            r = httpx.get(f"https://itunes.apple.com/lookup?id={','.join(ids[i:i+100])}&country={cc}",
                          timeout=30, headers=headers, proxy=proxy,
                          follow_redirects=True)
            r.raise_for_status()
            for app in r.json().get("results", []):
                w = (app.get("sellerUrl") or "").strip()
                if not w.startswith("http"):
                    continue
                host = (urlsplit(w).hostname or "").lower()
                key = entity_key(host) if host else ""
                if not key or key in seen_hosts:
                    continue
                seen_hosts.add(key)
                name = (app.get("sellerName") or "").strip()
                lines.append(f"{w}\t{name}" if name else w)
            time.sleep(1)
    return _write(out, lines)


# Overpass 公共端点（免费、限速；CN 网络如被拒走 HTTPS_PROXY）
_OVERPASS = "https://overpass-api.de/api/interpreter"

# 公共端点间歇 504/429 限速：主站 + 两镜像轮换，各退避重试一次
_OVERPASS_ENDPOINTS = (_OVERPASS,
                       "https://overpass.kumi.systems/api/interpreter",
                       "https://overpass.private.coffee/api/interpreter")
_UA = "youzi-leadhub/0.1 (BSP leadgen; polite; youzi99013@gmail.com)"


def _cn_proxy() -> str | None:
    """Overpass 出口代理：BSP_PROXY/HTTPS_PROXY 显式 > 本机 clash 7890 > 直连。

    2026-09-30 实测：CN 网络直连 Overpass 被 reset（GFW），走 clash 1.5s 即回包；
    海外 VPS 探测不到 clash 自动直连——不与任何机器绑定。
    """
    explicit = os.environ.get("BSP_PROXY") or os.environ.get("HTTPS_PROXY")
    if explicit:
        return explicit
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 7890), timeout=0.3):
            return "http://127.0.0.1:7890"
    except OSError:
        return None


def overpass_post(q: str, deadline_s: float = 60.0) -> dict:
    """Overpass 查询：三端点轮换 + 429/504 退避重试（osm/osm_direct 共用）。

    2026-10-01 加总 deadline（默认 60s）：之前 60s×6 attempts = 6 分钟/单 query
    卡死——印尼 4 bbox × 8 query（5 国）= 48 分钟最坏（智能爬取 OSM seed 永远
    "爬取中"，前端只看到 OSM 在跑 = "只爬海外地图商户" 假象）。
    deadline 内任何时刻超时即放弃——失败暴露给 reaper 收割 + 下轮自动重试。

    2026-09-30 修复：CN 直连被 reset 时三端点退避重试 = 无声挂数分钟再全失败。
    探测到本机代理就走代理，超时 60s 缩短到 20s（实测 5-30s）。
    """
    import time

    proxy = _cn_proxy()
    deadline = time.monotonic() + deadline_s
    last: Exception | None = None
    for endpoint in _OVERPASS_ENDPOINTS:
        if time.monotonic() >= deadline:
            break
        for attempt in (1, 2):
            remaining = max(deadline - time.monotonic(), 1.0)
            if remaining <= 1.0:
                break
            # ponytail: 单次超时取剩余 deadline 与 20s 较小值——
            # 否则 3 端点 × 2 重试 × 20s = 120s 物理上超出 deadline 仍会卡死
            try:
                r = httpx.post(endpoint, data={"data": q}, timeout=min(20.0, remaining),
                               headers={"User-Agent": _UA}, proxy=proxy)
                if r.status_code in (429, 504) and attempt == 1:
                    if time.monotonic() + 5 < deadline:
                        time.sleep(5)
                        continue
                    break
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                last = e
                if e.response.status_code not in (429, 504, 502, 503):
                    raise
                if e.response.status_code in (429, 504) and attempt == 1 \
                        and time.monotonic() + 5 < deadline:
                    time.sleep(5)
            except httpx.HTTPError as e:   # 网络层错误 → 换端点
                last = e
    raise last if last else RuntimeError("overpass unreachable")

# 印尼全域 area 查询超公共端点承载力（504 实测）——bbox 分 4 片（south,west,north,east）
# 2026-09-30 修复：爪哇东边界 115.7 → 118.0，覆盖 Bali (lon 115)/Lombok (116)/
# Sumbawa (117) 等主岛群——之前这些区域的餐厅/酒店全漏检
_ID_BBOXES = [(-6.0, 95.0, 6.0, 106.0),      # 苏门答腊
              (-9.5, 105.0, -5.8, 118.0),    # 爪哇 + Bali/Lombok/Sumbawa
              (-4.5, 108.0, 4.5, 119.0),     # 加里曼丹
              (-11.0, 118.0, 1.5, 141.0)]    # 苏拉威西以东


def osm(countries: str, limit: int, out: Path) -> Path:
    """OSM Overpass 渠道：本地商家（shop/餐饮/咖啡馆/住宿/药房…）带 website 标签 → 种子。

    leadhub 遗产复活：曾产出 1208 线索 / 72 条 wa.me 的被砍通道（leadhub 诊断
    R1——唯一规模验证过的路线）。OSM 商家天然是"本地 SMB 挂 WA 迎客"人群。
    countries: ISO3166-1 代码逗号分隔（MY,TH,PH,ID…）；limit 为总目标数（按国均摊）。

    2026-10-01 燃料扩容：①amenity 联合 +hotel/guest_house/pharmacy/fuel/bakery/
    hairdresser/car_repair（WA 重度 SMB 垂直；`out tags N` 无分页，同参重查只回
    同一批头部——扩联合是同参提产的唯一免费手段）；②host 级去重（实测 96 URL
    仅 68 独立 host，familymart/kfc 连锁同 host 十几条霸位）。
    # ponytail: 同参新鲜度天花板仍在头部 N 条——突破需 bbox 细分轮换，渠道再加码时做
    """
    from app.normalize import entity_key

    codes = [c.strip().upper() for c in countries.split(",") if c.strip()]
    per = max(limit // max(len(codes), 1), 50)
    urls: list[str] = []
    seen_hosts: set[str] = set()

    def _add(ws: list[str]) -> None:
        for w in ws:
            host = (urlsplit(w).hostname or "").lower()
            key = entity_key(host) if host else ""
            if not key or key in seen_hosts:
                continue
            seen_hosts.add(key)
            urls.append(w)

    _AMENITY = ("restaurant|cafe|fast_food|hotel|guest_house|pharmacy|fuel|"
                "bakery|hairdresser|car_repair")
    for code in codes:
        # ["website"] 标签存在性过滤：只取有官网的商家（实测无过滤时仅 ~18% 带 website）
        # 印尼走 bbox 分片（area 查询必 504），片间均摊配额
        if code == "ID":
            for bbox in _ID_BBOXES:
                q = (f'[out:json][timeout:180];'
                     f'(nwr["shop"]["website"]{bbox};'
                     f'nwr["amenity"~"^({_AMENITY})$"]["website"]{bbox};);'
                     f'out tags {max(per // len(_ID_BBOXES), 50)};')
                _add(_osm_query(q))
        else:
            q = (f'[out:json][timeout:180];area["ISO3166-1"="{code}"]->.a;'
                 f'(nwr["shop"]["website"](area.a);'
                 f'nwr["amenity"~"^({_AMENITY})$"]["website"](area.a););'
                 f'out tags {per};')
            _add(_osm_query(q))
    return _write(out, urls)


def _osm_query(q: str) -> list[str]:
    return [w.strip() for el in overpass_post(q).get("elements", [])
            if (w := (el.get("tags", {}).get("website") or "").strip()).startswith("http")]
