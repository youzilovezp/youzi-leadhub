"""必应国际版（ensearch=1，国内直连）商业意图词形探针：量中国供应商站产出率。"""
import asyncio
import base64
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "collectors"))
from app.collectors.web_search import drafts_with_stats  # noqa: E402

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def unwrap_bing_ck(url: str) -> str:
    """www.bing.com/ck/a?!&u=a1<base64url> → 真实 URL（ensearch 国际版是跳转包装）。"""
    m = re.search(r"[?&]u=a1([A-Za-z0-9_-]+)", url)
    if not m:
        return url
    pad = m.group(1) + "=" * (-len(m.group(1)) % 4)
    try:
        return base64.urlsafe_b64decode(pad).decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return url


def parse_b_algo(html: str) -> list[dict]:
    items = []
    for block in re.split(r'class="b_algo"', html)[1:]:
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        url = unwrap_bing_ck(m.group(1).strip())
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if url.startswith(("http://", "https://")) and title:
            items.append({"title": title, "url": url})
    return items


async def main() -> None:
    queries = [
        "wig manufacturer whatsapp contact",
        "LED strip factory whatsapp",
        "pet products supplier china whatsapp",
    ]
    async with httpx.AsyncClient(
        headers={"User-Agent": _UA, "Accept-Language": "en,zh-CN;q=0.9"},
        timeout=httpx.Timeout(15.0, connect=10.0),
        trust_env=False,
    ) as c:
        for q in queries:
            try:
                r = await c.get(
                    "https://cn.bing.com/search",
                    params={"q": q, "ensearch": "1"},
                    follow_redirects=True,
                )
                items = parse_b_algo(r.text)
                drafts, stats = drafts_with_stats(items, True)
                print(
                    f"[ensearch] {q!r} -> HTTP {r.status_code}, {len(items)} 条,"
                    f" {len(drafts)} 种子 (平台{stats['platform_domain']} 内容{stats['article_page']})"
                )
                for d in drafts[:6]:
                    print("   ", d.name[:44], "|", d.website, "| is_cn:", d.is_cn)
            except httpx.HTTPError as e:
                print(f"[ensearch] {q!r} -> {type(e).__name__}")
            await asyncio.sleep(4)


if __name__ == "__main__":
    asyncio.run(main())
