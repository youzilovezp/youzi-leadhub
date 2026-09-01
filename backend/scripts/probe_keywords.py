"""关键词产出率探针：走线上同款 _search_bing + drafts_with_stats，量不同词形的种子产出。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.collectors.web_search import _BING_H2_RE, _BING_TAG_RE, _search_bing, drafts_with_stats
from app.collectors.normalize import extract_domain

_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


async def main(kws: list[str]) -> None:
    async with (
        httpx.AsyncClient(timeout=_TIMEOUT) as via_proxy,
        httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as direct,
    ):
        clients = (via_proxy, direct)
        for kw in kws:
            items, err = await _search_bing(clients, kw, 20)
            if err:
                print(f"[{kw}] 引擎失败: {err}")
            else:
                drafts, stats = drafts_with_stats(items, True)
                print(
                    f"[{kw}] {len(items)} 条 -> {len(drafts)} 种子"
                    f" (平台域{stats['platform_domain']} 内容页{stats['article_page']}"
                    f" 同域{stats['dup_domain']} 泛标题{stats.get('generic_title', 0)})"
                )
                for d in drafts[:5]:
                    print(f"    {d.name[:38]} | {d.website}")
            await asyncio.sleep(4)


if __name__ == "__main__":
    kws = [
        "跨境电商 独立站 客服",            # 现默认词基线
        "外贸 客服 wa.me",                 # WA 链接特征
        "外贸工厂 官网 联系我们",          # 联系页词形
        "跨境电商 独立站 客服 -site:zhihu.com -site:csdn.net -site:weibo.com",  # 排除符
        "跨境 电商 官网 客服 whatsapp",    # whatsapp 修饰
    ]
    asyncio.run(main(kws))
