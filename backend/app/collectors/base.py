"""采集器框架：LeadDraft / TaskContext / Collector 基类。

新增采集器三步：
    1. 在 collectors/ 下写类继承 Collector（实现 run()）
    2. 需要 dedupe/评分自动复用：产出 LeadDraft 并 ctx.emit(draft)；
       富化型采集器（改存量线索）直接在 run() 里改库
    3. collectors/__init__.py 的 _REGISTRY 注册
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.exceptions import BusinessError


@dataclass
class LeadDraft:
    """采集器产出的原始线索（未归一化、未去重）。"""

    source: str  # 来源标识：google_maps / website_enrich / job_posting / manual
    name: str
    country: str | None = None  # ISO2
    city: str | None = None
    industry: str | None = None
    address: str | None = None
    phone_raw: str | None = None
    website: str | None = None
    email: str | None = None
    social: dict[str, str] = field(default_factory=dict)
    whatsapp_url: str | None = None  # 检测到的 wa.me / 插件链接
    whatsapp_job: bool = False  # 采集器可直接断言「在招 WhatsApp 岗位」
    job_urls: list[str] = field(default_factory=list)


@dataclass
class TaskContext:
    """一次任务执行的上下文：参数、进度、日志、取消、线索落库。"""

    task_id: int
    params: dict[str, Any]
    emit: Callable[[LeadDraft], Awaitable[tuple[int, bool]]]  # 落库，返回 (lead_id, 是否新建)
    log: Callable[[str, str], Awaitable[None]]  # (level, message)
    set_total: Callable[[int], None]
    inc_progress: Callable[[int], None]  # 增量
    _cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    def check_cancelled(self) -> None:
        """采集循环里每个条目调用一次；被取消抛 CancelledError 走统一收尾。"""
        if self._cancel_event.is_set():
            raise asyncio.CancelledError()

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)


class Collector(ABC):
    """采集器基类。"""

    name: str = ""  # 注册键（任务表 collector 字段存的值）
    title: str = ""  # 前端展示名
    # 参数说明（前端动态渲染创建表单用）：[{key, label, required, placeholder, default}]
    param_schema: list[dict[str, Any]] = []

    def validate_params(self, params: dict[str, Any]) -> None:  # noqa: B027  可选钩子，默认不校验
        """创建任务时校验参数，非法直接 BusinessError。默认不校验。"""

    @abstractmethod
    async def run(self, ctx: TaskContext) -> None:
        """执行采集。异常向上抛 → 任务 failed；CancelledError → cancelled。"""


def require_params(params: dict[str, Any], *keys: str, collector: str) -> None:
    """参数必填校验的公共实现。"""
    for key in keys:
        if not str(params.get(key) or "").strip():
            raise BusinessError(code=40001, message=f"{collector} 采集器缺少必填参数：{key}")


def split_csv(value: str | None) -> list[str]:
    """逗号分隔参数 → 去空白的列表。"""
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


# 目标市场国家下拉选项（WhatsApp 高渗透市场优先 + 常用国家）。
# 多个采集器共用；前端 n-select 渲染，仍允许手输任意 ISO2（filterable+tag）。
COUNTRY_OPTIONS = [
    # 东南亚（核心市场）
    {"label": "🇲🇾 马来西亚 (MY)", "value": "MY"},
    {"label": "🇸🇬 新加坡 (SG)", "value": "SG"},
    {"label": "🇮🇩 印度尼西亚 (ID)", "value": "ID"},
    {"label": "🇹🇭 泰国 (TH)", "value": "TH"},
    {"label": "🇵🇭 菲律宾 (PH)", "value": "PH"},
    {"label": "🇻🇳 越南 (VN)", "value": "VN"},
    {"label": "🇧🇳 文莱 (BN)", "value": "BN"},
    {"label": "🇰🇭 柬埔寨 (KH)", "value": "KH"},
    {"label": "🇱🇦 老挝 (LA)", "value": "LA"},
    {"label": "🇲🇲 缅甸 (MM)", "value": "MM"},
    # 东亚
    {"label": "🇯🇵 日本 (JP)", "value": "JP"},
    {"label": "🇰🇷 韩国 (KR)", "value": "KR"},
    {"label": "🇨🇳 中国 (CN)", "value": "CN"},
    {"label": "🇭🇰 中国香港 (HK)", "value": "HK"},
    {"label": "🇹🇼 中国台湾 (TW)", "value": "TW"},
    # 南亚
    {"label": "🇮🇳 印度 (IN)", "value": "IN"},
    {"label": "🇵🇰 巴基斯坦 (PK)", "value": "PK"},
    {"label": "🇧🇩 孟加拉国 (BD)", "value": "BD"},
    {"label": "🇱🇰 斯里兰卡 (LK)", "value": "LK"},
    {"label": "🇳🇵 尼泊尔 (NP)", "value": "NP"},
    # 中东
    {"label": "🇦🇪 阿联酋 (AE)", "value": "AE"},
    {"label": "🇸🇦 沙特 (SA)", "value": "SA"},
    {"label": "🇶🇦 卡塔尔 (QA)", "value": "QA"},
    {"label": "🇰🇼 科威特 (KW)", "value": "KW"},
    {"label": "🇴🇲 阿曼 (OM)", "value": "OM"},
    {"label": "🇧🇭 巴林 (BH)", "value": "BH"},
    {"label": "🇹🇷 土耳其 (TR)", "value": "TR"},
    # 非洲
    {"label": "🇳🇬 尼日利亚 (NG)", "value": "NG"},
    {"label": "🇰🇪 肯尼亚 (KE)", "value": "KE"},
    {"label": "🇬🇭 加纳 (GH)", "value": "GH"},
    {"label": "🇿🇦 南非 (ZA)", "value": "ZA"},
    {"label": "🇪🇬 埃及 (EG)", "value": "EG"},
    {"label": "🇲🇦 摩洛哥 (MA)", "value": "MA"},
    {"label": "🇹🇿 坦桑尼亚 (TZ)", "value": "TZ"},
    {"label": "🇪🇹 埃塞俄比亚 (ET)", "value": "ET"},
    # 拉美
    {"label": "🇧🇷 巴西 (BR)", "value": "BR"},
    {"label": "🇲🇽 墨西哥 (MX)", "value": "MX"},
    {"label": "🇨🇴 哥伦比亚 (CO)", "value": "CO"},
    {"label": "🇦🇷 阿根廷 (AR)", "value": "AR"},
    {"label": "🇨🇱 智利 (CL)", "value": "CL"},
    {"label": "🇵🇪 秘鲁 (PE)", "value": "PE"},
    {"label": "🇪🇨 厄瓜多尔 (EC)", "value": "EC"},
    {"label": "🇺🇾 乌拉圭 (UY)", "value": "UY"},
    {"label": "🇵🇾 巴拉圭 (PY)", "value": "PY"},
    {"label": "🇧🇴 玻利维亚 (BO)", "value": "BO"},
    {"label": "🇵🇦 巴拿马 (PA)", "value": "PA"},
    {"label": "🇨🇷 哥斯达黎加 (CR)", "value": "CR"},
    {"label": "🇬🇹 危地马拉 (GT)", "value": "GT"},
    {"label": "🇩🇴 多米尼加 (DO)", "value": "DO"},
    # 欧洲
    {"label": "🇬🇧 英国 (GB)", "value": "GB"},
    {"label": "🇪🇸 西班牙 (ES)", "value": "ES"},
    {"label": "🇵🇹 葡萄牙 (PT)", "value": "PT"},
    {"label": "🇩🇪 德国 (DE)", "value": "DE"},
    {"label": "🇫🇷 法国 (FR)", "value": "FR"},
    {"label": "🇮🇹 意大利 (IT)", "value": "IT"},
    {"label": "🇳🇱 荷兰 (NL)", "value": "NL"},
    {"label": "🇵🇱 波兰 (PL)", "value": "PL"},
    {"label": "🇬🇷 希腊 (GR)", "value": "GR"},
    # 大洋洲
    {"label": "🇦🇺 澳大利亚 (AU)", "value": "AU"},
    {"label": "🇳🇿 新西兰 (NZ)", "value": "NZ"},
]

# 国家 → 主要城市（城市输入框的联动建议；城市名用英文——采集查询语言）。
# 只是建议不是白名单：前端 filterable+tag，仍可输入任意城市。
CITY_OPTIONS_BY_COUNTRY: dict[str, list[str]] = {
    "MY": ["Kuala Lumpur", "George Town", "Johor Bahru", "Ipoh", "Shah Alam", "Kota Kinabalu", "Kuching", "Melaka"],
    "SG": ["Singapore"],
    "ID": ["Jakarta", "Surabaya", "Bandung", "Medan", "Semarang", "Yogyakarta", "Denpasar", "Makassar"],
    "TH": ["Bangkok", "Chiang Mai", "Phuket", "Pattaya", "Hat Yai", "Nakhon Ratchasima"],
    "PH": ["Manila", "Quezon City", "Cebu", "Davao", "Makati", "Baguio", "Iloilo", "Zamboanga"],
    "VN": ["Ho Chi Minh City", "Hanoi", "Da Nang", "Hai Phong", "Can Tho"],
    "BN": ["Bandar Seri Begawan"],
    "KH": ["Phnom Penh", "Siem Reap"],
    "LA": ["Vientiane"],
    "MM": ["Yangon", "Mandalay"],
    "JP": ["Tokyo", "Osaka", "Yokohama", "Nagoya", "Fukuoka", "Sapporo"],
    "KR": ["Seoul", "Busan", "Incheon", "Daegu"],
    "CN": ["Shanghai", "Beijing", "Guangzhou", "Shenzhen", "Hangzhou", "Chengdu"],
    "HK": ["Hong Kong"],
    "TW": ["Taipei", "Taichung", "Kaohsiung", "Tainan"],
    "IN": ["Mumbai", "Delhi", "Bangalore", "Chennai", "Hyderabad", "Kolkata", "Pune", "Ahmedabad"],
    "PK": ["Karachi", "Lahore", "Islamabad", "Faisalabad", "Rawalpindi"],
    "BD": ["Dhaka", "Chittagong", "Khulna"],
    "LK": ["Colombo", "Kandy"],
    "NP": ["Kathmandu", "Pokhara"],
    "AE": ["Dubai", "Abu Dhabi", "Sharjah", "Ajman"],
    "SA": ["Riyadh", "Jeddah", "Dammam", "Makkah", "Madinah"],
    "QA": ["Doha", "Al Rayyan"],
    "KW": ["Kuwait City", "Salmiya", "Jahra"],
    "OM": ["Muscat", "Salalah"],
    "BH": ["Manama", "Riffa"],
    "TR": ["Istanbul", "Ankara", "Izmir", "Antalya", "Bursa"],
    "NG": ["Lagos", "Abuja", "Ibadan", "Kano", "Port Harcourt"],
    "KE": ["Nairobi", "Mombasa", "Kisumu", "Nakuru"],
    "GH": ["Accra", "Kumasi", "Takoradi"],
    "ZA": ["Johannesburg", "Cape Town", "Durban", "Pretoria", "Port Elizabeth"],
    "EG": ["Cairo", "Alexandria", "Giza"],
    "MA": ["Casablanca", "Marrakesh", "Rabat", "Tangier"],
    "TZ": ["Dar es Salaam", "Dodoma", "Arusha"],
    "ET": ["Addis Ababa"],
    "BR": ["São Paulo", "Rio de Janeiro", "Brasília", "Belo Horizonte", "Curitiba", "Porto Alegre", "Salvador", "Fortaleza"],
    "MX": ["Mexico City", "Guadalajara", "Monterrey", "Puebla", "Tijuana", "Cancún"],
    "CO": ["Bogotá", "Medellín", "Cali", "Barranquilla", "Cartagena"],
    "AR": ["Buenos Aires", "Córdoba", "Rosario", "Mendoza", "La Plata"],
    "CL": ["Santiago", "Valparaíso", "Concepción", "Viña del Mar"],
    "PE": ["Lima", "Arequipa", "Trujillo", "Cusco"],
    "EC": ["Quito", "Guayaquil", "Cuenca"],
    "UY": ["Montevideo"],
    "PY": ["Asunción", "Ciudad del Este"],
    "BO": ["La Paz", "Santa Cruz", "Cochabamba"],
    "PA": ["Panama City"],
    "CR": ["San José"],
    "GT": ["Guatemala City"],
    "DO": ["Santo Domingo"],
    "GB": ["London", "Manchester", "Birmingham", "Liverpool", "Leeds", "Glasgow", "Edinburgh"],
    "ES": ["Madrid", "Barcelona", "Valencia", "Seville", "Zaragoza", "Málaga"],
    "PT": ["Lisbon", "Porto"],
    "DE": ["Berlin", "Munich", "Frankfurt", "Hamburg", "Cologne", "Düsseldorf", "Stuttgart"],
    "FR": ["Paris", "Lyon", "Marseille", "Toulouse", "Bordeaux", "Lille", "Nice"],
    "IT": ["Rome", "Milan", "Naples", "Turin", "Florence", "Bologna"],
    "NL": ["Amsterdam", "Rotterdam", "The Hague", "Utrecht", "Eindhoven"],
    "PL": ["Warsaw", "Kraków", "Wrocław", "Gdańsk", "Poznań"],
    "GR": ["Athens", "Thessaloniki"],
    "AU": ["Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide", "Gold Coast"],
    "NZ": ["Auckland", "Wellington", "Christchurch"],
}
