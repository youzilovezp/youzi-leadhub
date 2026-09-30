"""付费富化 Provider 抽象层（可选增强，默认关闭）。

免费主链路（seeds → 爬取 → 检测 → 落库）零依赖本模块；唯一调用方是
POST /api/crawler/enrich/{entity}（手动触发，每次恰 1 次 API 调用）——
不接入爬取管道、无任何自动触发路径。

2026-10-01 卫生修复（五代理审计）：
- 删除 BuiltWith/Snov/Apollo/Clearbit 四个纸面 stub——它们抽象方法未实现、
  不可实例化，TypeError 被 run_enrichment 吞成空 dict，配了 key 也静默无效。
  实装哪个再注册哪个（PROVIDERS 只含真实可用的 provider）。
- Hunter：删除 tech_signals 输出——domain-search 不返回技术栈，旧代码把
  pattern（邮箱格式字符串）按字符迭代成乱码写库。
- Docstring 纠偏：此前宣称的 EnrichmentPipeline(priority 400) 自动调度、
  YOUZI_ENRICHMENT_PROVIDERS 启用列表、CLI enrich 子命令均不存在。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import os

import httpx


class EnrichmentProvider(ABC):
    """付费富化 Provider 基类。

    子类必须实现：
    - `provider_name: str` 类属性（标识，进 PROVIDERS 注册表的键）
    - `enrich(entity_key, context) -> dict`（调 API 返回字段增量）

    约定：合法返回键 contact_email / tech_signals / revenue_range /
    employee_count / founded_year / industry / outreach_message；异常内部
    捕获降级返回空 dict——provider 失败不污染主管道。未配 KEY 时返回空 dict
    （零网络调用）。实装完成才允许加进 PROVIDERS 注册表。
    """

    provider_name: str = "base"

    @abstractmethod
    def enrich(self, entity_key: str, context: dict) -> dict:
        ...


class HunterProvider(EnrichmentProvider):
    """Hunter.io：按 domain 找企业邮箱（免费版 25 次搜索/月，$49/月 1000 次）。

    需 `YOUZI_HUNTER_API_KEY` 环境变量（每次调用时读取；进程 env 启动时固定，
    新 export 需重启进程）。文档：https://hunter.io/api-documentation/v2
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
            return {"contact_email": contact} if contact else {}
        except (httpx.HTTPError, KeyError, ValueError):
            return {}


# Provider 注册表：只收实装过的 provider（api 端点据此校验 provider 参数）
PROVIDERS: dict[str, type[EnrichmentProvider]] = {
    HunterProvider.provider_name: HunterProvider,
}


def run_enrichment(provider_name: str, entity_key: str, context: dict) -> dict:
    """单入口——按 provider_name 调对应 enrich() 并返回增量 dict。

    未注册的 provider 名返回空 dict（API 层在调用前已做 400 校验）。
    """
    cls = PROVIDERS.get(provider_name)
    if cls is None:
        return {}
    try:
        return cls().enrich(entity_key, context) or {}
    except Exception:
        return {}
