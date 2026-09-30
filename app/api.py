"""只读 API（FastAPI 轮子）：/api/stats、/api/leads + 静态托管前端产物。

启动：BSP_DB=data/leads.db uvicorn app.api:app --port 8788（8787 常被本机 Docker 占用）
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import db, stats
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


app = FastAPI(title="youzi-bsp", version="0.1.0",
              description="WhatsApp BSP 线索获取 · 只发现不外联")
app.add_middleware(CORSMiddleware,
                   allow_origins=["http://localhost:5173",
                                   "http://127.0.0.1:5173",
                                   "http://localhost:8788",
                                   "http://127.0.0.1:8788"],
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
    """
    from fastapi import HTTPException
    if body.status not in ("new", "contacted", "in_conversation", "won", "lost"):
        raise HTTPException(400, f"invalid status: {body.status}")
    conn = _conn(DB_PATH)
    ok = db.update_contact_status(conn, entity, body.status, body.notes)
    if not ok:
        raise HTTPException(404, f"entity not found: {entity}")
    conn.commit()
    return {"entity": entity, "status": body.status, "notes": body.notes}


@app.get("/api/stats")
def get_stats():
    return stats.channel_stats(_conn(DB_PATH))


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
        (*params, max(0, min(limit, 1000))),
    ).fetchall()
    return [dict(r) for r in rows]


# 前端构建产物存在则托管（开发态用 vite dev + /api 代理）
_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
