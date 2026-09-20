"""shoplazza_cases 采集器：Shoplazza 公开案例页（case-studies）。

数据源（2026-09-16 调研）：shoplazza.com/case-studies 列表页展示真实出海
品牌（Atumek/Hidizs/Alloyworks/Lefeet …）。每个案例内嵌在同一长列表页里
（含公司名 + 产品类目 + 销售增长 + CEO 引语）。

为什么有 ICP 价值：同 SHOPLINE——Shoplazza 服务中国出海独立站，入选「案例研究」
= 中国团队运营 + 已实现海外规模化 = BSP（出海 SaaS）目标客户。

联系方式不在案例页——线索落库后由自动接力走「官网发现 → 官网富化」
从公司自己的官网联系页拿（同 b2b_supplier / shopline_stories 模式）。
"""

from __future__ import annotations

import asyncio
import html as _html
import re
from typing import Any

import httpx

from app.collectors.base import Collector, LeadDraft, TaskContext
from app.core.config import settings

_BASE = "https://www.shoplazza.com"
_LIST_URL = f"{_BASE}/case-studies"
_CASE_GAP = 3.0
_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# 案例公司名（"Atumek\n\nAtumek's main products..." 形态——H2/H3 + 段落模板）
# 简单做法：从已知 4 个案例名 + 列表页的 <h2>/<h3> 文本扫描
_KNOWN_CASES = ("Atumek", "Hidizs", "Alloyworks", "Lefeet")
_H_TAG_RE = re.compile(r"<h[1-3][^>]*>(.*?)</h[1-3]>", re.S)
# CEO 引语（Evelyn Ji, Head of Global Growth / Tamson Tan, CEO / Mr. Wang, CEO and founder）
_CEO_RE = re.compile(
    r'([A-Z][a-zA-Z\u4e00-\u9fff]+(?:\s+[A-Z][a-zA-Z\u4e00-\u9fff]+)?)'
    r',\s*(?:CEO|Head of|Head|Co-?Founder|Founder|Director)'
)


def _clean_text(fragment: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_case_studies_page(html: str) -> list[dict[str, Any]]:
    """单页 case-studies（无分页）→ [{name, ceo, industry_hint?}]。

    实现策略：从 HTML 文本里提取「独立的公司名 token」+ CEO 引语对。
    启发式：每个案例以 H2/H3 标题开头（公司名），正文段落里包含「<Name>, CEO」引语。
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    text = html or ""
    for name in _KNOWN_CASES:
        if name in seen:
            continue
        # 验证该名字真的在 HTML 里出现（避免空爬）
        if name not in text:
            continue
        # 抓 CEO 引语（"<Name>, CEO" 或 "<Name>, Head of ..."）
        ceo_match = _CEO_RE.search(text)
        ceo = None
        if ceo_match and ceo_match.group(1) in text.split(name, 1)[-1][:3000]:
            # 启发式：引语必须出现在公司名上下文附近（前后 3000 字符）
            idx = text.find(name)
            window = text[max(0, idx - 200):idx + 3000]
            m = _CEO_RE.search(window)
            if m:
                ceo = m.group(1)
        out.append({"name": name, "ceo": ceo})
        seen.add(name)
    return out


def _industry_hint(name: str) -> str | None:
    """case-studies 主页的产品类目提示（不是结构化字段，是页面人工写的句子）。"""
    table = {
        "Atumek": "办公/IT 配件",
        "Hidizs": "消费电子/音频",
        "Alloyworks": "汽车配件",
        "Lefeet": "水上运动",
    }
    return table.get(name)


class ShoplazzaCasesCollector(Collector):
    name = "shoplazza_cases"
    title = "Shoplazza 公开案例研究（中国出海独立站）"
    logic_note = (
        "【抓什么】shoplazza.com/case-studies 单页案例研究（含 Atumek/Hidizs/"
        "Alloyworks/Lefeet 等 4 家精选）。每家都是「中国出海 + 已规模化」商家，"
        "命中 BSP（出海 SaaS）目标画像。\n"
        "【怎么抓】案例全部在同一长列表页（不分页）→ 解析公司名（硬编码 KNOWN_CASES"
        "白名单 + 文本特征）+ CEO 引语。联系方式不在案例页，留给 website_enrich "
        "从公司自己的官网联系页拿。\n"
        "【词怎么填】无关键词参数：精选案例是固定 4 家（营销页人工策展）；"
        "扩量请走 web_search 找未上榜商家。\n"
        "【准确性】入选「案例研究」= Shoplazza 官方背书 = 高质量线索；"
        "去重依赖 upsert_lead 自动处理（同 shopline_stories）。\n"
        "【建议节奏】每周 1 次定时跑（Shoplazza 更新频率不高）。\n"
        "【边界】单页无分页；3s 礼貌间隔防压测。"
    )
    param_schema = [
        {
            "key": "max_cases",
            "label": "每轮最多处理案例数",
            "required": False,
            "type": "number",
            "placeholder": "默认 20（Shoplazza 当前精选 4 家，留余地防增量）",
            "default": "20",
        },
    ]

    async def run(self, ctx: TaskContext) -> None:
        try:
            budget = max(1, min(int(ctx.params.get("max_cases") or 20), 50))
        except ValueError:
            budget = 20

        # 双 client：直连 + 代理兜底（同 shopline_stories，海外站常被 Cloudflare 挑战）
        proxy_url = settings.CRAWLER_PROXY_URL or ""
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _noop():
            yield None

        proxy_cm = (
            httpx.AsyncClient(
                headers={"User-Agent": _UA, "Accept-Language": "en,zh-CN;q=0.9"},
                timeout=_TIMEOUT,
                trust_env=False,
                follow_redirects=True,
                proxy=proxy_url,
            )
            if proxy_url
            else _noop()
        )
        async with httpx.AsyncClient(
            headers={"User-Agent": _UA, "Accept-Language": "en,zh-CN;q=0.9"},
            timeout=_TIMEOUT,
            trust_env=False,
            follow_redirects=True,
        ) as direct, proxy_cm as proxy:
            ctx.check_cancelled()
            html = await _fetch(direct, _LIST_URL)
            if html is None and proxy is not None:
                await ctx.log(
                    "info",
                    f"列表页直连被 Cloudflare 挑战，自动切代理 {proxy_url} 重试",
                )
                html = await _fetch(proxy, _LIST_URL)
            if html is None:
                raise BusinessError_shop(
                    code=50001,
                    message=f"列表页抓取失败：{_LIST_URL}（直连 + 代理都失败）",
                )
            cases = parse_case_studies_page(html)[:budget]
            ctx.set_total(len(cases))
            await ctx.log("info", f"列表页 → {len(cases)} 个案例")

            created = merged = 0
            for case in cases:
                ctx.check_cancelled()
                slug = case["name"].lower()
                draft = build_draft(case)
                if not draft:
                    ctx.inc_progress(1)
                    continue
                lead_id, is_created = await ctx.emit(draft)
                if is_created:
                    created += 1
                    await ctx.log(
                        "info",
                        f"✅ {case['name']}（新建 #{lead_id}, CEO={case.get('ceo') or '未抓到'}）",
                    )
                else:
                    merged += 1
                    await ctx.log(
                        "info",
                        f"↪️ {case['name']}（合并到 #{lead_id}）",
                    )
                ctx.inc_progress(1)
                await asyncio.sleep(_CASE_GAP)

            await ctx.log(
                "info",
                f"任务完成：{len(cases)} 个案例 → 新建 {created}、 合并 {merged}",
            )


async def _fetch(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        r = await client.get(url)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    return r.text


def build_draft(case: dict[str, Any]) -> LeadDraft | None:
    name = (case.get("name") or "").strip()
    if len(name) < 2:
        return None
    return LeadDraft(
        source=f"shoplazza_cases:{name.lower()}",
        name=name[:255],
        is_cn=True,
        industry=_industry_hint(name),
    )


class BusinessError_shop(Exception):  # noqa: N801
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(message)