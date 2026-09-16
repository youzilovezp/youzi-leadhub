"""alibaba_supplier 采集器：阿里巴巴国际站 B2B 出口供应商发现（2026-09-15 暂未启用）。

漏斗方向（与 b2b_supplier 同思路）：挂单在 B2B 目录上的都是中国出口工厂——
「中国企业 × 出海」两个资格天然成立，按品类词去目录领名单即可。

设计状态：暂未启用。

为什么没有启用（2026-09-15 实测）：
    阿里国际站 2024+ 完全 SPA 化 —— 搜索结果页 + 公司档案页 + 商家详情页都返回
    SPA shell（"404-Error"或空白骨架），真实数据由 JS 渲染。直接抓 HTML 只能拿到
    shell 和反爬探针（SetObjectPrototype.toString）。b2B 站反爬机制：
        - Cookie + 风控（强制滑块 CAPTCHA 频次）
        - JS 渲染 + 自定义 hook
        - 真实数据需 Playwright 渲染 + 反爬 bypass 训练

要走通需要：
    1. Playwright 浏览器渲染（重资源，每个搜索页 + 商家页都要新页面）
    2. 阿里滑块验证码绕过（CAPTCHA 训练模型 + Cookie 池）
    3. 限流 1req/2s + 代理池（避免单 IP 触发风控）

这相当于独立工程，不是「再加个 collector」。

替代方案（已就位）：
    - b2b_supplier 走 made-in-china.com —— HTML 直接含数据，无反爬
    - 搜索引擎发现（web_search + meta_ads 未来恢复）找新公司

如果未来要走阿里：
    1. 申请阿里 Open Platform Partner Token（isv.aliyun.com）
    2. 用 Search Alibaba API（partner 协议，返回 JSON，不用 JS）
    3. 写 alibaba_open_search_collector.py —— 完全不同的实现
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.collectors.base import Collector, LeadDraft, TaskContext


_DISABLED_REASON = (
    "阿里国际站 2024+ 完全 SPA 化（搜索/档案/详情都是 JS 渲染 + 反爬），"
    "直接抓 HTML 拿不到真实数据。要走通需 Playwright + 反爬训练 + 代理池——"
    "独立工程。当前 disabled（采集器未在 _REGISTRY 注册），本文件仅作骨架参考。"
)


class AlibabaSupplierCollector(Collector):
    """阿里国际站 B2B 出口供应商发现（实验性，未启用）。

    启用需要：
      1. 注册阿里 Open Platform Partner Token（isv.aliyun.com → 搜索 API）
      2. 实现 partner 协议搜索 → JSON 结果（不走 HTML 抓取）
      3. 重写 _search_products() 与 _parse_supplier() 用 API 返回的 JSON

    当前保留此类只为了：
      - param_schema 描述采集器的能力（前端创建任务时显示选项 + 帮助文案）
      - 通过 require_params 拒绝启动，给清晰错误提示
    """
    name = "alibaba_supplier"
    title = "B2B 出口目录（阿里巴巴国际站 — 暂未启用）"
    logic_note = _DISABLED_REASON
    param_schema = [
        {
            "key": "keywords",
            "label": "品类关键词",
            "required": False,
            "type": "tags",
            "placeholder": "英文品类词（LED lights, wig, pet products, outdoor furniture）"
            "——同 made-in-china 默认词",
            "default": "LED lights,wig,pet products,outdoor furniture,hair extension",
        },
        {
            "key": "max_suppliers",
            "label": "每轮最多处理供应商数",
            "required": False,
            "type": "number",
            "default": "30",
            "placeholder": "限流 30（阿里反爬严于 made-in-china，启用后默认 1req/2s）",
        },
    ]

    def validate_params(self, params: dict[str, Any]) -> None:
        from app.core.exceptions import BusinessError

        raise BusinessError(
            code=40001,
            message=(
                "alibaba_supplier 暂未启用（2026-09-15 验证阿里国际站完全 SPA 化，"
                "HTML 抓取拿不到数据，需 Playwright + 反爬训练，独立工程）。"
                "如需启用，先申请阿里 Open Platform Partner Token 再重写此 collector。"
            ),
        )

    async def run(self, ctx: TaskContext) -> None:
        # 双保险：validate_params 已拒绝，但 run() 入口也加 _DISABLED 显式错误
        # 防止 validate_params 被绕过（前端 schema 字段变更后忘了调用 validate_params）
        from app.core.exceptions import BusinessError

        raise BusinessError(
            code=50001,
            message=_DISABLED_REASON,
        )


# 模块加载时打印提示，让任何 import 这模块的人都看到「未启用」
import logging

_logger = logging.getLogger(__name__)
_logger.warning("alibaba_supplier collector imported but DISABLED — see module docstring")
