"""WA-first 发现通道探针：找「页面含 wa.me/86 中国号码 WhatsApp 链接」的外贸联系页。

通道候选（国内直连可达、免费零 key）：
1. cn.bing.com + ensearch=1（必应国际版结果，国内直连不经代理）
2. www.bing.com + cc=us（区域参数强制国际结果）
量词形：「"wa.me" 86 品类词」/「"api.whatsapp.com/send" 86 品类词」
"""
import asyncio
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def parse_b_algo(html: str) -> list[dict]:
    items = []
    for block in re.split(r'class="b_algo"', html)[1:]:
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        url = m.group(1).strip()
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if url.startswith(("http://", "https://")) and title:
            items.append({"title": title, "url": url})
    return items


async def fetch(client: httpx.AsyncClient, url: str, params: dict) -> tuple[int, list[dict]]:
    try:
        r = await client.get(url, params=params, follow_redirects=True)
        return r.status_code, parse_b_algo(r.text)
    except httpx.HTTPError as e:
        return 0, [{"title": f"ERR {type(e).__name__}", "url": str(e)[:80]}]


async def main() -> None:
    queries = [
        '"wa.me" 86 假发',
        '"api.whatsapp.com/send" 86 LED',
        '"wa.me/86" manufacturer',
    ]
    async with httpx.AsyncClient(
        headers={"User-Agent": _UA, "Accept-Language": "en,zh-CN;q=0.9"},
        timeout=httpx.Timeout(15.0, connect=10.0),
        trust_env=False,
    ) as c:
        for q in queries:
            for name, url, extra in (
                ("ensearch=1", "https://cn.bing.com/search", {"ensearch": "1"}),
                ("bing cc=us", "https://www.bing.com/search", {"cc": "us"}),
            ):
                params = {"q": q, **extra}
                status, items = await fetch(c, url, params)
                print(f"[{name}] {q!r} -> HTTP {status}, {len(items)} 条")
                for it in items[:5]:
                    print("   ", it["title"][:48], "|", it["url"][:72])
                await asyncio.sleep(4)


if __name__ == "__main__":
    asyncio.run(main())
