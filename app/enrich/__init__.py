"""付费富化 Provider 抽象层（Hunter / Snov / Apollo / BuiltWith / Clearbit）。

设计原则（2026-09-30 付费扩展预留）：
- 每个 Provider 一个文件（<100 行），只实现 `enrich()` 一个方法
- 当前所有 Provider 为 **空 stub**：enrich() 返回空 dict；接入第一个付费 API 时
  按 Adapter 模式填实，**零业务代码改动**
- Provider 不在 `pipelines.WaStorePipeline` 里塞——新 `EnrichmentPipeline`（priority 400）
  异步触发，单 item 入库后按 `enrichment_status='none'` 调度
- 限速在 Provider 内部实现（避免污染主下载中间件栈）
- Adapter 通过 `settings.YOUZI_ENRICHMENT_PROVIDERS = ["hunter", ...]` 启用
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import json
import os
import time
from urllib.parse import quote

import httpx


class EnrichmentProvider(ABC):
    """付费富化 Provider 基类。

    每个 Provider 子类必须实现：
    - `provider_name: str` 类属性（标识）
    - `enrich(entity_key, context) -> dict` 实例方法（调 API 返回字段增量）
    """

    provider_name: str = "base"

    @abstractmethod
    def enrich(self, entity_key: str, context: dict) -> dict:
        """调用付费 API，返回字段增量。

        Args:
            entity_key: 实体键（e.g. "example.com"），用于查 API
            context: 已落库的 domain 行 dict（含 channel/score/market/p0/developer_name
                     等），Provider 可基于此过滤或加权

        Returns:
            dict 含要更新到 domain 表的字段——空 dict = 跳过（无富化结果或应忽略）。
            合法键：contact_email / tech_signals / revenue_range / employee_count /
                   founded_year / industry / outreach_message。
            异常应**内部捕获降级**返回空 dict——provider 失败不应污染主管道。
        """
        ...

    def should_enrich(self, row: dict) -> bool:
        """默认策略：P0 或高分（>=10）优先富化。Provider 可按需覆盖。

        资源有限时只富化最有价值的 lead，避免配额浪费。
        """
        if row.get("enrichment_status") == "done":
            return False  # 已富化过不重复（除非 Provider 自己显式重跑）
        return bool(row.get("p0")) or (row.get("score") or 0) >= 10


class HunterProvider(EnrichmentProvider):
    """Hunter.io：按 domain 找企业邮箱 + tech 栈（$49/月 1000 calls）。

    2026-10-01：接入实现——需要 `YOUZI_HUNTER_API_KEY` 环境变量。
    文档：https://hunter.io/api-documentation/v2

    返回示例：
    {
        "contact_email": "info@example.com",
        "tech_signals": "shopify, stripe, ga",   # Hunter 也返回 tech
    }
    """
    provider_name = "hunter"

    def enrich(self, entity_key: str, context: dict) -> dict:
        api_key = os.environ.get("YOUZI_HUNTER_API_KEY")
        if not api_key:
            return {}      # 没配 key 直接降级返回空（不污染主管道）
        try:
            r = httpx.get(
                "https://api.hunter.io/v2/domain-search",
                params={"domain": entity_key, "api_key": api_key, "limit": 5},
                timeout=15,
            )
            if r.status_code != 200:
                return {}
            data = r.json().get("data") or {}
            emails = data.get("emails") or []
            contact = next((e["value"] for e in emails
                            if e.get("value") and e.get("confidence", 0) > 50), None)
            pattern = data.get("pattern") or []
            tech = ",".join(p for p in pattern if p)[:200] or None
            return {k: v for k, v in
                    {"contact_email": contact, "tech_signals": tech}.items() if v}
        except (httpx.HTTPError, KeyError, ValueError):
            return {}


class SnovProvider(EnrichmentProvider):
    """Snov.io：邮箱查找 + LinkedIn 拓展（$39/月）。"""
    provider_name = "snov"


class ApolloProvider(EnrichmentProvider):
    """Apollo.io：联系人 + 公司数据（$49/月）。返回 employee/revenue/industry。"""
    provider_name = "apollo"


class BuiltWithProvider(EnrichmentProvider):
    """BuiltWith：技术栈识别（$295/月）。返回 cms/ecommerce/cdn/analytics → tech_signals JSON。"""
    provider_name = "builtwith"


class ClearbitProvider(EnrichmentProvider):
    """Clearbit Reveal：公司数据（$99/月）。返回 employee_count/revenue_range/founded_year/industry。"""
    provider_name = "clearbit"


# Provider 注册表（CLI `python -m app enrich --provider hunter` 用）
PROVIDERS: dict[str, type[EnrichmentProvider]] = {
    cls.provider_name: cls
    for cls in (HunterProvider, SnovProvider, ApolloProvider,
                BuiltWithProvider, ClearbitProvider)
}


def run_enrichment(provider_name: str, entity_key: str, context: dict) -> dict:
    """2026-10-01：单入口——按 provider_name 调对应 enrich() 并返回增量 dict。

    内部捕获异常→返回空 dict。CLI / API 共用。
    """
    cls = PROVIDERS.get(provider_name)
    if cls is None:
        return {}
    try:
        return cls().enrich(entity_key, context) or {}
    except Exception:
        return {}
