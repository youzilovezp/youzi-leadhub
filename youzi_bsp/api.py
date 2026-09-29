"""只读 API（FastAPI 轮子）：/api/stats、/api/leads + 静态托管前端产物。

启动：BSP_DB=data/leads.db uvicorn youzi_bsp.api:app --port 8788（8787 常被本机 Docker 占用）
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from youzi_bsp import db, stats
from youzi_bsp.crawl_api import router as crawl_router

DB_PATH = os.environ.get("BSP_DB", "data/leads.db")


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
                   allow_methods=["GET", "POST"],
                   allow_credentials=False)
app.include_router(crawl_router)


@app.get("/api/stats")
def get_stats():
    return stats.channel_stats(_conn(DB_PATH))


@app.get("/api/leads")
def get_leads(limit: int = 200, channel: str | None = None, p0: int | None = None,
              market_group: str | None = None):
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
                   d.lang, d.p0, d.score, d.widget, d.developer_name,
                   GROUP_CONCAT(DISTINCT s.e164) AS phones
            FROM domain d
            JOIN sighting s ON s.entity_key = d.entity_key
            {wsql}
            GROUP BY d.entity_key
            ORDER BY d.score DESC, d.entity_key
            LIMIT ?""",
        (*params, max(0, min(limit, 1000))),  # 负 limit 在 SQLite = 无限
    ).fetchall()
    return [dict(r) for r in rows]


# 前端构建产物存在则托管（开发态用 vite dev + /api 代理）
_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
