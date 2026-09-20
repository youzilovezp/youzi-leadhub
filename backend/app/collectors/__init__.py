"""采集器注册表。新增采集器在此追加一行即可复用任务体系/去重/评分。

2026-09-13：移除 meta_ads + web_search——
  - meta_ads 等 Ads Library API 审核（短期不能用）
  - web_search 需要 Google/Bing API key（用户当前无法申请）
两者留 register 会在数据源管理页显示「活跃但 0 leads」假阳性承诺。
后续激活走 git revert + 加 collector 到 _REGISTRY 即可。
"""

from __future__ import annotations

from app.collectors.b2b_supplier import B2BSupplierCollector
from app.collectors.base import Collector, LeadDraft, TaskContext
from app.collectors.career_site import CareerSiteCollector
from app.collectors.cifnews_cases import CifnewsCasesCollector  # noqa: F401  保留代码（列表 API 受限未启用，见 logic_note）
from app.collectors.job_posting import JobPostingCollector
from app.collectors.shoplazza_cases import ShoplazzaCasesCollector
from app.collectors.shopline_stories import ShoplineStoriesCollector  # noqa: F401  保留代码（Cloudflare 限制未启用，见 logic_note）
from app.collectors.website_enrich import WebsiteEnrichCollector

# ponytail: shopline_stories / cifnews_cases 不注册——
#   shopline_stories: 列表页被 Cloudflare v3 挡住（实测 2026-09-16 直连/代理/headless 都 403）
#   cifnews_cases: 列表页 HTML 空 + Playwright 拿不到 + 内部 API（api.cifnews.com/page/ajax/important?key=theme_detail）返回 data=[]
# 两者都「单篇 article HTML 可抓但没有列表源」——留代码不暴露 UI，避免前端创建任务后跑空。
# 等以后绕过（cookie 注入 / 走国内镜像 / 列表 API 公开）再注册。
_REGISTRY: dict[str, Collector] = {
    c.name: c
    for c in (
        JobPostingCollector(),
        CareerSiteCollector(),
        B2BSupplierCollector(),
        ShoplazzaCasesCollector(),
        WebsiteEnrichCollector(),
    )
}


def get_collector(name: str) -> Collector | None:
    return _REGISTRY.get(name)


def list_collectors() -> list[dict]:
    """采集器元信息（前端创建任务表单动态渲染 + 数据源管理页展示爬取逻辑）。"""
    return [
        {
            "name": c.name,
            "title": c.title,
            "params": c.param_schema,
            "logic_note": getattr(c, "logic_note", "") or "",
        }
        for c in _REGISTRY.values()
    ]


__all__ = [
    "Collector",
    "LeadDraft",
    "TaskContext",
    "get_collector",
    "list_collectors",
    "_REGISTRY",
]
