"""只读 API（FastAPI 轮子）：/api/stats、/api/leads + 静态托管前端产物。

启动：BSP_DB=data/leads.db uvicorn app.api:app --port 8788（8787 常被本机 Docker 占用）
"""
from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import db, stats
from app import crawl_api
from app.crawl_api import router as crawl_router

DB_PATH = os.environ.get("BSP_DB", "data/leads.db")


# 2026-09-30 销售 UX：按 market_group 自动选语言生成开场白（spec §3.7 "按需"实现）
# MVP 用模板而非 LLM（零成本，零调用），LLM 接入走 enrichment_status='llm_ready' 路径
_OUTREACH_TEMPLATES = {
    "SEA": "Hi, I noticed you're using WhatsApp Business — we help BSPs in {country} automate customer service and team collaboration. Open to a quick chat?",
    "LATAM": "Olá! Vi que vocês usam WhatsApp Business. Ajudamos empresas no {country} a automatizar atendimento. Tem interesse?",
    "MENA": "مرحباً، لاحظت استخدامكم لـ WhatsApp Business. نساعد الشركات في {country} على أتمتة خدمة العملاء.",
    "EU": "Hello, I noticed your team uses WhatsApp Business for customer contact. We help EU companies scale BSP workflows. Open to a chat?",
    "CN": "您好，看到贵司使用 WhatsApp Business 触达客户。我们提供 BSP 一站式服务（账号 / 客服 SaaS / 精细化运营），方便聊一下吗？",
}


def _build_outreach(lead: dict) -> dict:
    """按 lead 信息生成开场白草稿（销售每天用 20+ 次，必须 < 50ms 返回）。"""
    market_group = lead.get("market_group") or "EU"
    country = lead.get("market") or "your region"
    entity = lead.get("entity_key") or ""
    template = _OUTREACH_TEMPLATES.get(market_group, _OUTREACH_TEMPLATES["EU"])
    message = template.format(country=country)
    return {
        "message": message,
        "language": {"SEA": "id", "LATAM": "pt", "MENA": "ar", "EU": "en", "CN": "zh"}.get(market_group, "en"),
        "whatsapp_deep_link": _build_wa_link(lead.get("phones", "")),
        "entity": entity,
    }


def _build_wa_link(phones: str) -> str | None:
    """从 phones 字段（逗号分隔 E.164）取第一个号码生成 wa.me 链接。"""
    if not phones:
        return None
    first = phones.split(",")[0].strip().lstrip("+").replace(" ", "")
    return f"https://wa.me/{first}" if first else None


# 复用连接：db.connect 含建表 DDL + 迁移检查，逐请求重跑纯属浪费；
# FastAPI sync 端点跑线程池，须 check_same_thread=False（只读查询，SQLite 串行化安全）
@lru_cache(maxsize=1)
def _conn(path: str):
    return db.connect(path, check_same_thread=False)


# ============================================================================
# 2026-09-30 智能爬取加固（grilling 共识 Q9/Q12）：
#   启动 = ① flock 单实例守卫（单 worker/单 data 目录铁律）② 收割线程（孤儿收割 + seed→crawl 自动接续）
# ============================================================================
_API_LOCK: list = []  # 持引用防 GC 关闭 fd（进程退出自动释放）


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    import fcntl
    lock_path = Path("data") / ".api.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        raise RuntimeError(
            f"另一实例已持有 {lock_path}（单 worker/单实例铁律：并发写 data/ 会互毁）")
    _API_LOCK.append(fh)
    crawl_api.start_reaper()
    yield


app = FastAPI(title="youzi-bsp", version="0.1.0",
              description="WhatsApp BSP 线索获取 · 只发现不外联",
              lifespan=_lifespan)
# 跨机访问（局域网 IP / 域名）通过 BSP_CORS_ORIGINS 覆盖，逗号分隔
_cors_env = os.environ.get("BSP_CORS_ORIGINS")
_origins = ([o.strip() for o in _cors_env.split(",") if o.strip()] if _cors_env
            else ["http://localhost:5173",
                  "http://127.0.0.1:5173",
                  "http://localhost:8788",
                  "http://127.0.0.1:8788"])
app.add_middleware(CORSMiddleware,
                   allow_origins=_origins,
                   allow_methods=["GET", "POST", "PATCH"],
                   allow_credentials=False)
app.include_router(crawl_router)


# ============================================================================
# 销售 UX 重构（2026-09-30）：API 围绕"今天要联系谁"+"怎么联系"展开
# ============================================================================

class ContactUpdate(BaseModel):
    """销售标记跟进状态：new/contacted/in_conversation/won/lost。
    notes 可选——简单备注（如"已报价"）。"""
    status: str  # 'new' | 'contacted' | 'in_conversation' | 'won' | 'lost'
    notes: str | None = None


@app.get("/api/today")
def get_today_queue(limit: int = Query(20, ge=1, le=100)):
    """今日联系队列：P0 优先 + 未联系过的新线索。

    销售的核心需求——>首页打开就能看到今天要联系谁。
    排序：score DESC（高分在前）；P0 永远置顶。
    返回字段含 outreach_message 草稿 + 完整 WA 链接 + 信号说明。
    """
    conn = _conn(DB_PATH)
    rows = conn.execute(
        """SELECT d.entity_key, d.market, d.market_group, d.lang, d.score, d.p0,
                  d.developer_name, d.widget, d.widget as widget_text,
                  d.contact_status, d.contact_notes,
                  GROUP_CONCAT(DISTINCT s.e164) AS phones,
                  d.outreach_message
           FROM domain d
           JOIN sighting s ON s.entity_key = d.entity_key
           WHERE d.contact_status = 'new'
           GROUP BY d.entity_key
           ORDER BY d.p0 DESC, d.score DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["outreach"] = _build_outreach(d)
        out.append(d)
    return out


@app.get("/api/progress")
def get_progress():
    """本周进度：销售 quota 完成情况 + 状态分布。"""
    conn = _conn(DB_PATH)
    # 本周联系数
    week_contacts = conn.execute(
        """SELECT COUNT(*) FROM domain
           WHERE contact_status IN ('contacted','in_conversation','won','lost')
             AND contacted_at >= datetime('now', '-7 days')""").fetchone()[0]
    # 状态分布
    by_status = {row["contact_status"]: row["c"] for row in conn.execute(
        "SELECT contact_status, COUNT(*) AS c FROM domain GROUP BY contact_status")}
    return {
        "week_contacts": week_contacts,
        "by_status": by_status,
        "total": sum(by_status.values()),
        "new_today": conn.execute(
            "SELECT COUNT(*) FROM domain WHERE contact_status='new'").fetchone()[0],
    }


@app.patch("/api/leads/{entity}/status")
def patch_lead_status(entity: str, body: ContactUpdate):
    """销售标记跟进状态（销售每天点几十次）。

    PATCH 而非 POST——状态是"更新"语义。
    2026-09-30 修复：_conn 是 lru_cache 共享连接，FastAPI 线程池并发 PATCH 时
    同一连接并发写 → sqlite3.InterfaceError（连接打坏后连锁 500）——写路径加锁。
    """
    from fastapi import HTTPException
    if body.status not in ("new", "contacted", "in_conversation", "won", "lost"):
        raise HTTPException(400, f"invalid status: {body.status}")
    with _WRITE_LOCK:
        conn = _conn(DB_PATH)
        ok = db.update_contact_status(conn, entity, body.status, body.notes)
        if not ok:
            raise HTTPException(404, f"entity not found: {entity}")
        conn.commit()
    return {"entity": entity, "status": body.status, "notes": body.notes}


# 2026-09-30：PATCH 并发写锁（见 patch_lead_status 注释）
_WRITE_LOCK = threading.Lock()


@app.get("/api/stats")
def get_stats():
    return stats.channel_stats(_conn(DB_PATH))


# ============================================================================
# 2026-09-30 智能爬虫（Smart Crawler）：ROI 调度 + 自动选 channel
# ============================================================================

@app.get("/api/crawler/strategy")
def get_crawler_strategy():
    """每个 channel 的 ROI 评分 + 推荐（前端「智能推荐」面板用）。

    评分逻辑见 app/smart_crawler.py。返回倒序（高分在前）+ 总览统计。
    sample 是占位 channel（手工上传），无 ROI 不计。
    """
    from app import smart_crawler
    conn = _conn(DB_PATH)
    stats_map = {s["channel"]: s for s in db.get_all_crawl_stats(conn)}
    ranked = smart_crawler.rank_channels(
        ("myshopify", "play", "osm"),
        stats_map,
    )
    return {
        "channels": [vars(s) for s in ranked],
        "best": vars(ranked[0]) if ranked else None,
        "tuning": {
            "backoff_threshold": smart_crawler.BACKOFF_THRESHOLD,
            "backoff_minutes": smart_crawler.BACKOFF_DURATION_MINUTES,
        },
    }


@app.post("/api/crawler/clear-backoff/{channel}")
def clear_channel_backoff(channel: str):
    """手动清除某 channel 冷却（admin 用——确认 channel 恢复后调用）。"""
    conn = _conn(DB_PATH)
    db.clear_backoff(conn, channel)
    conn.commit()
    return {"channel": channel, "backoff_until": None}


@app.post("/api/crawler/expand/{channel}")
def auto_expand_seed(channel: str, country: str = "", limit: int = 500):
    """2026-09-30 L2 Auto-expansion：种池耗尽时 admin 一键补种。

    spawn seeds.py 子进程补新种子到 data/seeds-{channel}.txt（追加，不覆盖）。
    """
    from app import smart_crawler as _sc
    if channel not in ("myshopify", "play", "osm"):
        raise HTTPException(400, f"未知 channel: {channel}")
    if limit < 50 or limit > 5000:
        raise HTTPException(400, f"limit 应在 50-5000，got {limit}")
    job = _sc.auto_expand_seed(channel, country=country, limit=limit,
                               cwd=str(os.getcwd()))
    return {
        "ok": True,
        "channel": channel,
        "country": country,
        "limit": limit,
        "job": job,
        "hint": "种子已 spawn，3-15 分钟完成；完成后到跑批 Tab 触发增量爬取",
    }


@app.get("/api/leads")
def get_leads(limit: int = Query(200, ge=1, le=2000,
                                description="最大返回 2000 条（前端导出按钮文案对齐）"),
              channel: str | None = None, p0: int | None = None,
              market_group: str | None = None):
    # 2026-09-30 修复：limit 上限显式 2000（与前端导出按钮文案 "≤ 2000 条" 一致）；
    # 之前 Pydantic 不限，传 limit=2000 被 SQL 静默截到 1000，前端拿不全数据。
    conn = _conn(DB_PATH)
    where, params = [], []
    if channel:
        where.append("d.channel = ?")
        params.append(channel)
    if p0 is not None:
        where.append("d.p0 = ?")
        params.append(p0)
    if market_group:
        # 落库时一律存大写（_market_group() 实现），查询也规范成大写避免漏匹配
        where.append("d.market_group = ?")
        params.append(market_group.upper())
    wsql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"""SELECT d.entity_key AS entity, d.channel, d.market, d.market_group,
                   d.lang, d.p0, d.score, d.widget, d.email, d.developer_name,
                   d.enrichment_status, d.tech_signals,
                   GROUP_CONCAT(DISTINCT s.e164) AS phones
            FROM domain d
            JOIN sighting s ON s.entity_key = d.entity_key
            {wsql}
            GROUP BY d.entity_key
            ORDER BY d.score DESC, d.entity_key
            LIMIT ?""",
        (*params, max(0, min(limit, 2000))),
    ).fetchall()
    return [dict(r) for r in rows]


# 前端构建产物存在则托管（开发态用 vite dev + /api 代理）
_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
