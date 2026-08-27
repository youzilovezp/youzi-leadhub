"""osm_overpass 采集器：OpenStreetMap Overpass API 免费商家采集。

数据源定位（vs google_maps）：
    纯免费 + 可商用（ODbL，展示线索需署名 © OpenStreetMap contributors）。
    覆盖约为 Google 的 5-10%（KL 实测：有 website 的 POI 约 4000 条），
    但带 website/phone 标签的 POI 是被认真维护的商家，质量不差；
    contact:whatsapp 标签 = 白送的高意向信号（无需富化直接命中）。

链路：Nominatim 地理编码（城市 → bbox）→ Overpass bbox 查询
    （城市 × 行业 一次查询，默认只采有官网的可富化线索）→ 标签映射 LeadDraft。

爬虫纪律（公共实例的生存法则，实测校准 2026-08）：
    - 自定义 UA 必须：默认 python UA 一律 406，且 HTTP 头只允许 ASCII
    - 串行 + 每次查询间隔 ≥2s；Nominatim 限 1 req/s
    - 429 尊重 Retry-After；504/超时 → 指数退避 + 轮换镜像（de 实例易过载）
    - 服务端 [timeout:120] 截断时响应带 remark → 记 warn 不算失败
    - 双通道网络：OSM 公共 API 与 website_enrich 相反——继承系统代理优先
      （国内网络 Nominatim 直连被重置，走代理才通），连接级失败自动换
      直连兜底；镜像轮换 × 双通道 = 单查询最多 2×镜像数 次尝试

去重陷阱：OSM 小商家常把 Facebook 主页填进 website——社媒链接必须归
social 而非 website，否则 extract_domain 得到 facebook.com，所有 FB 商家
被 dedupe_key 合并成一条（与 job_posting 的 _classify_url 同款防御）。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.collectors.base import (
    COUNTRY_OPTIONS,
    Collector,
    LeadDraft,
    TaskContext,
    require_params,
    split_csv,
)
from app.collectors.normalize import normalize_phone
from app.core.exceptions import BusinessError

# UA 必须纯 ASCII（HTTP 头编码限制）——ODbL 署名放在任务日志/展示层，不进请求头
_UA = "youzi-leadhub/0.1 (OpenStreetMap Overpass lead collection; ODbL attribution in product UI)"
_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_MIRRORS = (
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
# 行业预设：value=行业 token（存进 Lead.industry 的词表），selectors=Overpass
# 标签选择器（value 支持正则 alternation）。行业比顶层键（shop/amenity）细一级，
# 是「销售可按行业话术跟进」的粒度；_RAW_KEYS 兜底整键扫描（高级用法）。
_INDUSTRY_PRESETS: list[dict] = [
    {
        "value": "food",
        "label": "🍽 餐饮（餐厅/咖啡/快餐）",
        "selectors": ['["amenity"~"^(restaurant|cafe|fast_food|food_court|ice_cream|bar|pub)$"]'],
    },
    {
        "value": "retail",
        "label": "🛍 零售商店",
        "selectors": [
            '["shop"~"^(supermarket|convenience|clothes|bakery|hardware|electronics|'
            "mobile_phone|jewellery|florist|gift|shoes|sports|toys|stationery|books|"
            'butcher|greengrocer|furniture|department_store)$"]'
        ],
    },
    {
        "value": "beauty",
        "label": "💄 美容美发",
        "selectors": ['["shop"~"^(beauty|hairdresser|cosmetics|massage)$"]'],
    },
    {
        "value": "health",
        "label": "🏥 医疗健康（诊所/医院/药房）",
        "selectors": ['["shop"~"^(pharmacy|optician|medical_supply)$"]', '["amenity"~"^(clinic|hospital|doctors)$"]', '["healthcare"]'],
    },
    {
        "value": "dental",
        "label": "🦷 牙科诊所",
        "selectors": ['["shop"="dentist"]', '["amenity"="dentist"]', '["healthcare"="dentist"]'],
    },
    {
        "value": "hotel",
        "label": "🏨 酒店住宿",
        "selectors": ['["tourism"~"^(hotel|guest_house|hostel|motel)$"]'],
    },
    {
        "value": "education",
        "label": "🎓 教育培训",
        "selectors": ['["amenity"~"^(school|college|kindergarten|language_school|driving_school|music_school)$"]'],
    },
    {
        "value": "car",
        "label": "🚗 汽车服务",
        "selectors": ['["shop"~"^(car_repair|car|tyres|car_parts|motorcycle)$"]', '["amenity"~"^(car_wash|fuel)$"]'],
    },
    {
        "value": "realestate",
        "label": "🏠 房产中介",
        "selectors": ['["shop"="estate_agent"]'],
    },
    {
        "value": "finance_law",
        "label": "⚖️ 法律财税",
        "selectors": ['["shop"~"^(lawyer|accounting|tax_advisor|notary)$"]'],
    },
    {
        "value": "travel",
        "label": "✈️ 旅行社",
        "selectors": ['["shop"="travel_agency"]'],
    },
    {
        "value": "fitness",
        "label": "🏋️ 健身运动",
        "selectors": ['["leisure"~"^(fitness_centre|sports_centre)$"]'],
    },
    {
        "value": "pets",
        "label": "🐾 宠物服务",
        "selectors": ['["shop"="pet"]', '["amenity"="veterinary"]'],
    },
    {
        "value": "craft",
        "label": "🛠 手工/维修作坊",
        "selectors": ['["craft"]'],
    },
    {
        "value": "office",
        "label": "🏢 公司企业（office）",
        "selectors": ['["office"]'],
    },
    {
        "value": "shop_all",
        "label": "🛒 全部商店（shop 整键）",
        "selectors": ['["shop"]'],
    },
    {
        "value": "amenity_all",
        "label": "🎯 全部便民设施（amenity 整键）",
        "selectors": ['["amenity"]'],
    },
]
_PRESET_BY_VALUE = {p["value"]: p for p in _INDUSTRY_PRESETS}
_RAW_KEYS = ("shop", "amenity", "healthcare", "office", "craft", "tourism", "leisure")
# POI 主行业键的取值优先级（Lead.industry 存 OSM 标签值，如 dentist/restaurant）
_PRIMARY_KEYS = ("shop", "healthcare", "amenity", "office", "craft", "tourism", "leisure")
_DEFAULT_CATEGORIES = "food,retail,beauty,health"

# OSM 标签值 → 中文名（Lead.industry 的展示映射；库存筛选/表格列共用，单一数据源）。
# 未收录的值原样显示——OSM 词表开放，宁可英文兜底也不瞎翻译。
INDUSTRY_LABELS_ZH: dict[str, str] = {
    # shop 零售/服务
    "supermarket": "超市", "convenience": "便利店", "department_store": "百货商场", "mall": "购物中心",
    "clothes": "服装店", "shoes": "鞋店", "boutique": "精品店", "jewellery": "珠宝店", "watches": "钟表店",
    "bakery": "面包店", "butcher": "肉铺", "greengrocer": "蔬果店", "seafood": "水产店", "confectionery": "糖果店",
    "coffee": "咖啡豆店", "tea": "茶叶店", "alcohol": "酒类商店", "wine": "葡萄酒庄",
    "electronics": "电子产品店", "computer": "电脑店", "mobile_phone": "手机店", "hifi": "音响店",
    "furniture": "家具店", "interior_decoration": "室内装饰", "kitchen": "厨具店", "bed": "床品店",
    "hardware": "五金店", "doityourself": "DIY 用品", "garden_centre": "园艺中心", "florist": "花店",
    "gift": "礼品店", "toys": "玩具店", "sports": "运动用品店", "stationery": "文具店", "books": "书店",
    "newsagent": "报刊亭", "music": "音像店", "musical_instrument": "乐器店", "photo": "照相馆",
    "laundry": "洗衣店", "dry_cleaning": "干洗店", "tailor": "裁缝店", "travel_agency": "旅行社",
    "estate_agent": "房产中介", "deli": "熟食店", "frozen_food": "冷冻食品店", "cheese": "奶酪店",
    # shop 美业/健康
    "beauty": "美容店", "hairdresser": "美发店", "cosmetics": "化妆品店", "massage": "按摩店",
    "tattoo": "纹身店", "perfumery": "香水店",
    "dentist": "牙科诊所", "pharmacy": "药店", "optician": "眼镜店", "medical_supply": "医疗用品店",
    "hearing_aids": "助听器店", "herbalist": "草药店", "nutrition_supplements": "营养品店",
    "pet": "宠物店", "pet_grooming": "宠物美容", "agrarian": "农资店",
    # shop 汽车/其他
    "car": "汽车销售", "car_repair": "汽修店", "car_parts": "汽配店", "tyres": "轮胎店",
    "motorcycle": "摩托车行", "caravan": "房车行", "boat": "船具店", "bicycle": "自行车店",
    "outdoor": "户外用品店", "copyshop": "打印店", "funeral_directors": "殡葬服务", "money_transfer": "汇款服务",
    # amenity 餐饮
    "restaurant": "餐厅", "cafe": "咖啡馆", "fast_food": "快餐店", "food_court": "美食广场",
    "ice_cream": "冰淇淋店", "bar": "酒吧", "pub": "酒馆", "biergarten": "啤酒园", "nightclub": "夜店",
    # amenity 医疗/教育/金融
    "clinic": "诊所", "hospital": "医院", "doctors": "门诊", "veterinary": "宠物医院",
    "school": "学校", "college": "学院", "university": "大学", "kindergarten": "幼儿园",
    "language_school": "语言学校", "driving_school": "驾校", "music_school": "音乐学校", "dance_school": "舞蹈学校",
    "bank": "银行", "bureau_de_change": "货币兑换", "post_office": "邮局",
    # amenity 汽车及其他
    "fuel": "加油站", "car_wash": "洗车店", "charging_station": "充电站", "parking": "停车场",
    "coworking_space": "联合办公", "events_venue": "活动场地", "community_centre": "社区中心",
    # healthcare 值
    "alternative": "替代医学", "midwife": "助产士", "physiotherapist": "物理治疗", "psychotherapist": "心理治疗",
    "laboratory": "检验实验室", "optometrist": "验光中心", "rehabilitation": "康复中心", "blood_donation": "献血站",
    "counselling": "心理咨询", "audiologist": "听力中心", "speech_therapist": "语言治疗",
    # office 值
    "company": "公司", "it": "IT 公司", "financial": "金融公司", "insurance": "保险公司",
    "lawyer": "律所", "accountant": "会计", "consulting": "咨询公司", "advertising_agency": "广告公司",
    "architect": "建筑师事务所", "engineering": "工程公司", "surveyor": "测量公司", "estate_agent_office": "房产公司",
    "educational_institution": "教育机构", "travel_agent": "旅行社", "employment_agency": "职业中介",
    "government": "政府机构", "ngo": "公益组织",
    # craft 值
    "carpenter": "木工", "joiner": "细木工", "electrician": "电工", "plumber": "水管工",
    "builder": "施工队", "roofing": "屋顶施工", "painter": "油漆工", "gardener": "园艺服务",
    "locksmith": "锁匠", "shoemaker": "修鞋店", "dressmaker": "缝纫店", "photographer": "摄影工作室",
    "winery": "酿酒坊", "sawmill": "锯木厂", "metal_construction": "金属加工",
    # tourism / leisure 值
    "hotel": "酒店", "guest_house": "民宿", "hostel": "青年旅舍", "motel": "汽车旅馆", "apartment": "公寓",
    "attraction": "景点", "museum": "博物馆", "theme_park": "主题乐园", "zoo": "动物园", "aquarium": "水族馆",
    "fitness_centre": "健身房", "sports_centre": "运动中心", "fitness_station": "健身角", "dance": "舞蹈中心",
    "amusement_arcade": "游戏厅", "escape_game": "密室逃脱", "bowling_alley": "保龄球馆",
}

# require_website 两种模式的服务端过滤（正则匹配 tag 键）：
#   true  → 有官网（后续 website_enrich 检测 WhatsApp 的前提）
#   false → 有任一联系方式（官网或电话），避免全量 amenity 的长椅/厕所噪音
_CONTACT_KEYS_STRICT = "^(website|contact:website)$"
_CONTACT_KEYS_LOOSE = "^(website|contact:website|phone|contact:phone|contact:mobile|contact:whatsapp)$"

_QUERY_GAP = 2.0  # Overpass 相邻查询间隔（公共实例礼貌上限）
_GEOCODE_GAP = 1.0  # Nominatim 1 req/s
_MAX_ATTEMPTS = 6  # 单查询总尝试次数 = 3 镜像 × 2 通道全覆盖
_TIMEOUT = httpx.Timeout(150.0, connect=15.0)  # 客户端兜底；服务端另有 [timeout:120]

_SOCIAL_HOSTS = {
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "linkedin.com": "linkedin",
    "tiktok.com": "tiktok",
}


def _split_website_social(website: str | None) -> tuple[str | None, dict[str, str]]:
    """website 值可能是社媒主页 → 归 social。返回 (website, social)。

    OSM 数据里商家把 FB 主页填 website 极常见；不拆会让 domain:facebook.com
    吞掉所有 FB 商家（dedupe 合并成一条）。
    """
    if not website:
        return None, {}
    host = urlparse(website if "://" in website else f"https://{website}").netloc.lower()
    host = host.removeprefix("www.")
    platform = next((p for dom, p in _SOCIAL_HOSTS.items() if host == dom or host.endswith("." + dom)), None)
    if platform:
        return None, {platform: website}
    return website, {}


def _first_tag(tags: dict[str, str], *keys: str) -> str | None:
    """取第一个非空标签值；OSM 多值标签用分号分隔，取第一项。"""
    for key in keys:
        value = (tags.get(key) or "").split(";")[0].strip()
        if value:
            return value
    return None


def build_query(bbox: str, industries: list[str], *, require_website: bool, keywords: list[str]) -> str:
    """构造 Overpass QL（行业预设 → 标签选择器 union）。纯函数（可单测）。

    [~"^(website|...)$"~"."] 按正则匹配「键」存在性；
    keywords 走 name 值的正则 alternation，服务端过滤减少传输。
    """
    contact_re = _CONTACT_KEYS_STRICT if require_website else _CONTACT_KEYS_LOOSE
    selectors: list[str] = []
    for ind in industries:
        preset = _PRESET_BY_VALUE.get(ind)
        if preset:
            selectors.extend(preset["selectors"])
        elif ind in _RAW_KEYS:  # 顶层键整扫（高级用法）
            selectors.append(f'["{ind}"]')
    if not selectors:
        selectors = ['["shop"]']
    kw_filter = ""
    if keywords:
        alt = "|".join(re.escape(k) for k in keywords)
        kw_filter = f'[~"^name$"~"{alt}",i]'
    body = "".join(f"nwr{sel}[~\"{contact_re}\"~\".\"]{kw_filter}({bbox});" for sel in selectors)
    return f"[out:json][timeout:120];({body});out tags;"


def parse_bbox(geo_json: list[dict[str, Any]]) -> str | None:
    """Nominatim 首个结果 → Overpass bbox 字符串（s,w,n,e）。无结果返回 None。"""
    if not geo_json:
        return None
    # Nominatim boundingbox 顺序 [south, north, west, east]，字符串
    south, _north, west, east = geo_json[0]["boundingbox"]
    return f"{south},{west},{float(_north):.4f},{float(east):.4f}"


def _whatsapp_url(raw: str | None, country: str | None) -> str | None:
    """contact:whatsapp 值（裸号 / +号 / wa.me 链接混写）→ 标准 wa.me 链接。"""
    tel = normalize_phone(raw, country)
    return f"https://wa.me/{tel.lstrip('+')}" if tel else None


def osm_to_draft(tags: dict[str, str], country: str, city: str) -> LeadDraft | None:
    """OSM 元素 tags → LeadDraft。无名元素返回 None（emit 侧也会跳过）。"""
    name = (tags.get("name") or "").strip()
    if not name:
        return None
    website_raw = _first_tag(tags, "website", "contact:website")
    website, social = _split_website_social(website_raw)
    wa_raw = _first_tag(tags, "contact:whatsapp")
    industry = next((tags[k] for k in _PRIMARY_KEYS if tags.get(k)), None)
    addr_parts = [
        " ".join(p for p in (tags.get("addr:housenumber"), tags.get("addr:street")) if p),
        tags.get("addr:postcode"),
        tags.get("addr:city") or city,
    ]
    return LeadDraft(
        source="osm_overpass",
        name=name,
        country=country,
        city=city,
        industry=industry,
        address=", ".join(p for p in addr_parts if p) or None,
        phone_raw=_first_tag(tags, "phone", "contact:phone", "contact:mobile"),
        website=website,
        email=_first_tag(tags, "email", "contact:email"),
        social=social,
        whatsapp_url=_whatsapp_url(wa_raw, country),
    )


class _MirrorPool:
    """轮换镜像：每次失败取下一个，成功后记住好用的实例（下次从它开始）。"""

    def __init__(self, mirrors: tuple[str, ...] = _MIRRORS) -> None:
        self._mirrors = mirrors
        self._idx = 0

    def next(self) -> str:
        mirror = self._mirrors[self._idx % len(self._mirrors)]
        self._idx += 1
        return mirror

    def mark_good(self, mirror: str) -> None:
        self._idx = self._mirrors.index(mirror)


def _parse_bool(raw: Any) -> bool:
    return str(raw).strip().lower() not in ("false", "0", "no", "")


class OsmOverpassCollector(Collector):
    name = "osm_overpass"
    title = "开源地图商家（免费）"
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
            "key": "categories",
            "label": "行业",
            "required": False,
            "type": "multiselect",
            "options": [{"label": p["label"], "value": p["value"]} for p in _INDUSTRY_PRESETS],
            "placeholder": "",
            "default": _DEFAULT_CATEGORIES,
        },
        {
            "key": "keywords",
            "label": "名称关键词",
            "required": False,
            "type": "tags",
            "placeholder": "选填，如 dental；过滤商家名称",
            "default": "",
        },
        {
            "key": "require_website",
            "label": "仅采有官网的",
            "required": False,
            "type": "switch",
            "placeholder": "",
            "default": "true",
        },
    ]

    def validate_params(self, params: dict[str, Any]) -> None:
        require_params(params, "country", "cities", collector=self.title)
        if len(str(params["country"]).strip()) != 2:
            raise BusinessError(code=40001, message="country 必须是 2 位国家代码，如 MY / PH")
        valid = set(_PRESET_BY_VALUE) | set(_RAW_KEYS)
        for cat in split_csv(str(params.get("categories") or _DEFAULT_CATEGORIES)):
            if cat not in valid:
                raise BusinessError(code=40001, message=f"不支持的行业：{cat}")

    async def run(self, ctx: TaskContext) -> None:
        country = str(ctx.params["country"]).strip().upper()
        cities = split_csv(str(ctx.params["cities"]))
        categories = split_csv(str(ctx.params.get("categories") or _DEFAULT_CATEGORIES))
        keywords = split_csv(str(ctx.params.get("keywords") or ""))
        require_website = _parse_bool(ctx.params.get("require_website", "true"))

        ctx.set_total(len(cities) * len(categories))
        industry_labels = [(_PRESET_BY_VALUE.get(c) or {}).get("label", c) for c in categories]
        await ctx.log("info", f"OSM 采集：{cities} × {industry_labels}（数据 © OpenStreetMap contributors, ODbL）")

        pool = _MirrorPool()
        queries_ok = 0
        headers = {"User-Agent": _UA, "Accept": "application/json"}
        # 双通道：代理通道优先（国内网络 Nominatim 直连被重置），直连兜底（无代理环境）
        async with (
            httpx.AsyncClient(headers=headers, timeout=_TIMEOUT) as via_proxy,
            httpx.AsyncClient(headers=headers, timeout=_TIMEOUT, trust_env=False) as direct,
        ):
            for i, city in enumerate(cities):
                ctx.check_cancelled()
                if i:
                    await asyncio.sleep(_GEOCODE_GAP)
                bbox = await self._geocode([via_proxy, direct], ctx, city, country)
                if bbox is None:
                    # 城市解析失败不致命：跳过并把该城市进度补满，避免进度卡死
                    await ctx.log("warn", f"城市地理编码失败，跳过：{city}")
                    ctx.inc_progress(len(categories))
                    continue
                for j, industry in enumerate(categories):
                    ctx.check_cancelled()
                    if (i, j) != (0, 0):
                        await asyncio.sleep(_QUERY_GAP)
                    elements = await self._fetch(
                        [via_proxy, direct], ctx, pool, build_query(bbox, [industry], require_website=require_website, keywords=keywords)
                    )
                    if elements is None:
                        continue
                    queries_ok += 1
                    for el in elements:
                        ctx.check_cancelled()
                        draft = osm_to_draft(el.get("tags") or {}, country, city)
                        if draft is not None:
                            await ctx.emit(draft)
                    label = (_PRESET_BY_VALUE.get(industry) or {}).get("label", industry)
                    await ctx.log(
                        "info", f"{city} [{label}] → {len(elements)} 个 POI（require_website={require_website}）"
                    )
                    ctx.inc_progress(1)

        # 假成功防护：一个查询都没成功 → failed 让用户重跑（对齐 job_posting）
        if queries_ok == 0:
            raise BusinessError(code=50001, message="全部 Overpass 查询失败（实例过载/网络异常），稍后重跑或减少城市数")

    async def _geocode(
        self, clients: list[httpx.AsyncClient], ctx: TaskContext, city: str, country: str
    ) -> str | None:
        """Nominatim：城市名 → bbox。限 1 req/s（外层已控制间隔），失败仅警告。"""
        params = {"q": city, "format": "json", "limit": 1, "countrycodes": country.lower()}
        for k, client in enumerate(clients):
            try:
                resp = await client.get(_NOMINATIM, params=params)
            except httpx.HTTPError as exc:
                await ctx.log("warn", f"Nominatim 异常（{city}，通道{k}）：{type(exc).__name__}")
                continue
            if resp.status_code == 200:
                return parse_bbox(resp.json())
            await ctx.log("warn", f"Nominatim HTTP {resp.status_code}（{city}，通道{k}）")
        return None

    async def _fetch(
        self, clients: list[httpx.AsyncClient], ctx: TaskContext, pool: _MirrorPool, query: str
    ) -> list[dict[str, Any]] | None:
        """带退避 + 镜像轮换 + 双通道的 Overpass 查询。全部尝试失败返回 None（上层继续）。"""
        for attempt in range(_MAX_ATTEMPTS):
            mirror = pool.next()
            # 偶数尝试走代理通道、奇数走直连；HTTP 层失败不换通道（镜像轮换负责容错）
            client = clients[attempt % len(clients)]
            status: int | None = None
            retry_after = 0.0
            try:
                resp = await client.get(mirror, params={"data": query})
                status = resp.status_code
                if status == 200:
                    data = resp.json()
                    if data.get("remark"):  # 服务端 timeout 截断等提示：结果不完整但不失败
                        await ctx.log("warn", f"Overpass 结果截断：{str(data['remark'])[:120]}")
                    pool.mark_good(mirror)
                    return data.get("elements", [])
                retry_after = float(resp.headers.get("retry-after") or 0)
            except httpx.HTTPError as exc:
                await ctx.log("warn", f"Overpass 网络异常（{mirror}）：{type(exc).__name__}")
            if status is not None:
                await ctx.log("warn", f"Overpass HTTP {status}（{mirror}），第 {attempt + 1} 次尝试")
            # 429 → 尊重 Retry-After；网络异常/5xx → 指数退避。两者都换镜像
            delay = max(retry_after, 2.0 * (2**attempt), 2.0) if status == 429 else 2.0 * (2**attempt)
            await asyncio.sleep(min(delay, 30.0))
        return None
