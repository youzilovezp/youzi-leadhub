"""google_maps 采集器：Google Places API v1 Text Search（官方付费通道）。

按「关键词 × 城市」笛卡尔积拼 textQuery（如 `dentist in Kuala Lumpur`），
每关键词最多翻 3 页（nextPageToken，约 60 条）。
"""

from __future__ import annotations

from typing import Any

import httpx

from app.collectors.base import (
    COUNTRY_OPTIONS,
    Collector,
    TaskContext,
    require_params,
    split_csv,
)
from app.core.config import settings
from app.core.exceptions import BusinessError

_API_URL = "https://places.googleapis.com/v1/places:searchText"
# 注意：Places API 条款对商家字段（非 place_id）有存储期限限制，商用前需确认
_FIELD_MASK = (
    "places.displayName,places.formattedAddress,places.internationalPhoneNumber,"
    "places.websiteUri,nextPageToken"
)
_MAX_PAGES = 3


class GoogleMapsCollector(Collector):
    name = "google_maps"
    title = "Google Maps 商家"
    param_schema = [
        {
            "key": "country",
            "label": "国家",
            "required": True,
            "type": "select",  # 可搜索 + 可手输任意 ISO2
            "options": COUNTRY_OPTIONS,
            "placeholder": "选择或输入 2 位国家码",
            "default": "",
        },
        {
            "key": "cities",
            "label": "城市",
            "required": True,
            "type": "cities",  # 与 country 联动：选国家后出城市建议，仍可手输任意城市
            "depends_on": "country",
            "placeholder": "先选国家；可输入建议城市或自定义",
            "default": "",
        },
        {
            "key": "keywords",
            "label": "行业关键词",
            "required": True,
            "type": "tags",
            "placeholder": "输入关键词回车，如 dental clinic",
            "default": "",
        },
    ]

    def validate_params(self, params: dict[str, Any]) -> None:
        require_params(params, "country", "cities", "keywords", collector=self.title)
        if len(str(params["country"]).strip()) != 2:
            raise BusinessError(code=40001, message="country 必须是 2 位国家代码，如 MY / PH")

    async def run(self, ctx: TaskContext) -> None:
        if not settings.GOOGLE_MAPS_API_KEY:
            raise BusinessError(code=40001, message="未配置 GOOGLE_MAPS_API_KEY，无法执行")

        country = str(ctx.params["country"]).strip().upper()
        cities = split_csv(str(ctx.params["cities"]))
        keywords = split_csv(str(ctx.params["keywords"]))
        ctx.set_total(len(cities) * len(keywords))

        async with httpx.AsyncClient(timeout=30) as client:
            for keyword in keywords:
                for city in cities:
                    ctx.check_cancelled()
                    query = f"{keyword} in {city}"
                    await ctx.log("info", f"搜索：{query}")
                    page_token: str | None = None
                    for _page in range(_MAX_PAGES):
                        body: dict[str, Any] = {"textQuery": query, "pageSize": 20}
                        if page_token:
                            body["pageToken"] = page_token
                        resp = await client.post(
                            _API_URL,
                            json=body,
                            headers={
                                "X-Goog-Api-Key": settings.GOOGLE_MAPS_API_KEY,
                                "X-Goog-FieldMask": _FIELD_MASK,
                            },
                        )
                        if resp.status_code != 200:
                            await ctx.log(
                                "error", f"API 错误 {resp.status_code}：{resp.text[:200]}"
                            )
                            break
                        data = resp.json()
                        places = data.get("places", [])
                        for place in places:
                            ctx.check_cancelled()
                            phone = place.get("internationalPhoneNumber")
                            await ctx.emit(_to_draft(place, phone, country, keyword, city))
                        page_token = data.get("nextPageToken")
                        if not page_token:
                            break
                    ctx.inc_progress(1)


def _to_draft(place: dict[str, Any], phone: str | None, country: str, keyword: str, city: str):
    from app.collectors.base import LeadDraft

    name = (place.get("displayName") or {}).get("text")
    if not name:
        return LeadDraft(source="google_maps", name="")  # emit 侧会跳过空名
    return LeadDraft(
        source="google_maps",
        name=name,
        country=country,
        city=city,
        industry=keyword,
        address=place.get("formattedAddress"),
        phone_raw=phone,
        website=place.get("websiteUri"),
    )
