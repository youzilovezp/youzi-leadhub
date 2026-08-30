"""采集器注册表。新增采集器在此追加一行即可复用任务体系/去重/评分。"""

from __future__ import annotations

from app.collectors.base import Collector, LeadDraft, TaskContext
from app.collectors.google_maps import GoogleMapsCollector
from app.collectors.job_posting import JobPostingCollector
from app.collectors.meta_ad_library import MetaAdsCollector
from app.collectors.web_search import WebSearchCollector
from app.collectors.website_enrich import WebsiteEnrichCollector

_REGISTRY: dict[str, Collector] = {
    c.name: c
    for c in (
        MetaAdsCollector(),
        WebSearchCollector(),
        GoogleMapsCollector(),
        JobPostingCollector(),
        WebsiteEnrichCollector(),
    )
}


def get_collector(name: str) -> Collector | None:
    return _REGISTRY.get(name)


def list_collectors() -> list[dict]:
    """采集器元信息（前端创建任务表单动态渲染）。"""
    return [
        {
            "name": c.name,
            "title": c.title,
            "params": c.param_schema,
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
