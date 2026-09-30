"""业务场景 → 技术参数映射（挖新人群，2026-09-30 UX 简化）。

why：销售不关心 ISO 国家码 / 类目英文名 —— 用业务语言描述目标人群，
后端自动映射到渠道/国家/类目。一个场景可同时触发多个渠道（play + osm 并行）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChannelPlan:
    """单个渠道的种子生成计划。"""
    channel: str            # 'play' | 'itunes' | 'osm'
    countries: str          # 逗号分隔 ISO 国家码
    categories: str = ""    # play/itunes 专用：逗号分隔类目
    per_country: int = 200  # 每个国家目标数（按国家均摊）


@dataclass(frozen=True)
class Scene:
    """业务场景：一键触发一个或多个渠道的种子生成。"""
    id: str                 # 内部 key，前端用作 enum
    label: str              # 卡片大标题
    pitch: str              # 销售话术（面向 WhatsApp BSP 销售）
    plans: tuple[ChannelPlan, ...]


# 5 个场景（按 BSP 销售实战，按目标市场分组）
# 2026-10-01 燃料扩容（五代理审计 P0-1：play 图头部静止 13 次补种 0 新 URL）：
# - play 类目 3→4（+FOOD_AND_DRINK，WA 重度 SMB 垂直）+ 深度 150→250
# - itunes 全场景入列（iOS 商家池独享、免费无 key 无 Node 依赖）
# - osm 国家补 sg（高消费 SMB 密集）+ 深度 200→300
SCENES: tuple[Scene, ...] = (
    Scene(
        id="sea_smb",
        label="东南亚小店",
        pitch="印尼/泰国/越南/菲律宾/马来 — WA 重度市场，本地小店挂 WA 接外卖和询盘",
        plans=(
            ChannelPlan("play", "id,th,vn,ph,my",
                        "BUSINESS,SHOPPING,COMMUNICATION,FOOD_AND_DRINK", per_country=250),
            ChannelPlan("itunes", "id,th,vn,ph,my",
                        "BUSINESS,SHOPPING,FOOD_AND_DRINK", per_country=200),
            ChannelPlan("osm",  "id,th,vn,ph,my,sg", per_country=300),
        ),
    ),
    Scene(
        id="latam_ecom",
        label="拉美电商",
        pitch="巴西/墨西哥/阿根廷/哥伦比亚 — 拉美电商商家用 WA 接单转化",
        plans=(
            ChannelPlan("play", "br,mx,ar,co,cl,pe",
                        "SHOPPING,COMMUNICATION,FOOD_AND_DRINK", per_country=250),
            ChannelPlan("itunes", "br,mx,ar,co,cl",
                        "SHOPPING,FOOD_AND_DRINK", per_country=200),
            ChannelPlan("osm",  "br,mx,ar,co", per_country=300),
        ),
    ),
    Scene(
        id="mena_biz",
        label="中东企业",
        pitch="阿联酋/沙特/埃及/卡塔尔/科威特 — 中东企业主用 WA Business 谈生意",
        plans=(
            ChannelPlan("play", "ae,sa,eg,qa,kw,bh",
                        "BUSINESS,COMMUNICATION,FOOD_AND_DRINK", per_country=250),
            ChannelPlan("itunes", "ae,sa,eg,qa,kw",
                        "BUSINESS,COMMUNICATION", per_country=200),
        ),
    ),
    Scene(
        id="africa_new",
        label="非洲新兴市场",
        pitch="尼日利亚/肯尼亚/南非/埃及 — 新兴市场 WA 渗透率高，竞对未触达",
        plans=(
            ChannelPlan("play", "ng,ke,za,gh,eg",
                        "BUSINESS,COMMUNICATION,FINANCE,FOOD_AND_DRINK", per_country=250),
            ChannelPlan("itunes", "ng,ke,za,gh",
                        "BUSINESS,FINANCE", per_country=200),
        ),
    ),
    Scene(
        id="id_food",
        label="印尼餐饮",
        pitch="印尼本地餐饮/咖啡馆 — 万岛之国，Warung 挂 WA 接外卖是标配",
        plans=(
            ChannelPlan("osm", "id", per_country=500),
            ChannelPlan("itunes", "id", "FOOD_AND_DRINK", per_country=200),
        ),
    ),
)


def get_scene(scene_id: str) -> Scene | None:
    for s in SCENES:
        if s.id == scene_id:
            return s
    return None
