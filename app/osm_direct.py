"""OSM 直标签导入：Overpass 元素自带 phone / contact:whatsapp 标签 → 直接线索。

旧 leadhub 的真实主力路径（诊断 R1：742 条线索来自 OSM 直标签，免爬取）。
合规留痕：sighting.url 记 OSM 元素链接、layer='osm'（区别于爬取证据 link/text）。
仅导入带 website 标签的元素（实体键需要真实域名）。
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from app import db
from app.normalize import entity_key, normalize_phone
from app.pipelines import score_pending
from app.seeds import overpass_post

# 电话来源标签优先级：contact:whatsapp 语义最强（明确是 WA 号）
_PHONE_TAGS = ("contact:whatsapp", "contact:mobile", "phone", "contact:phone")


def _wa_number(raw: str) -> str:
    """contact:whatsapp 可能是 wa.me 链接、api send 链接或裸号码——统一剥出号码串。"""
    raw = raw.strip()
    if raw.startswith("http"):
        parts = urlsplit(raw)
        if "wa.me" in parts.netloc:
            return parts.path.lstrip("/")
        if "whatsapp.com" in parts.netloc:
            for k, v in (q.split("=", 1) for q in parts.query.split("&") if "=" in q):
                if k == "phone":
                    return v
    return raw


def elements_to_rows(elements: list[dict]) -> list[dict]:
    """纯变换：Overpass elements → [{entity, e164, country, osm_url, market}]。
    仅保留有 website 且任一电话标签通过 E164 校验的元素。"""
    rows: list[dict] = []
    for el in elements:
        tags = el.get("tags", {})
        web = (tags.get("website") or "").strip()
        if not web.startswith("http"):
            continue
        host = (urlsplit(web).hostname or "").lower()
        if not host:
            continue
        raw = next((tags[t] for t in _PHONE_TAGS if tags.get(t)), None)
        if raw is None:
            continue
        ph = normalize_phone(_wa_number(raw), imply_plus=True)
        if not ph:
            continue
        e164, country = ph
        rows.append({
            "entity": entity_key(host),
            "e164": e164,
            "country": country,
            "osm_url": f"https://www.openstreetmap.org/{el.get('type')}/{el.get('id')}",
        })
    return rows


def import_osm(countries: str, limit: int, db_path: str | Path) -> int:
    """按国家查询 Overpass 直标签线索并入库（幂等 upsert，重复导入不产生重复 sighting）。"""
    codes = [c.strip().upper() for c in countries.split(",") if c.strip()]
    per = max(limit // max(len(codes), 1), 100)
    conn = db.connect(db_path)
    n = 0
    for code in codes:
        # phone 标签不限类目会查穿百万级元素（公共端点必 504）——限定 shop/餐饮商家，
        # 与种子渠道同一人群规模（实测 35s/国）
        q = (f'[out:json][timeout:180];area["ISO3166-1"="{code}"]->.a;'
             f'(nwr["contact:whatsapp"]["website"](area.a);'
             f'nwr["shop"]["phone"]["website"](area.a);'
             f'nwr["amenity"~"^(restaurant|cafe|fast_food)$"]["phone"]["website"](area.a););'
             f'out tags {per};')
        r = overpass_post(q)
        for row in elements_to_rows(r.get("elements", [])):
            db.upsert_domain(conn, row["entity"], "osm", None)
            db.upsert_phone(conn, row["e164"], row["country"])
            db.upsert_sighting(conn, row["entity"], row["e164"], row["osm_url"], "osm")
            n += 1
        conn.commit()
    # 打分走 close_spider 同款 DB 派生路径（P1 修复）：不在导入时用空 flags 算分
    # ——否则覆盖同实体已爬取的 icp/developer_name 派生结果，且漏写 market_group
    score_pending(conn)
    conn.commit()
    conn.close()
    return n
