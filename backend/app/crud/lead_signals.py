"""信号级证据链（PRD §4.1）：lead_signals 表的写入与查询。

写入是 upsert 语义：(lead_id, signal_type, value) 唯一——已存在刷 last_seen，
不存在插入。证据链是追加型事实记录，不做删除（重跑/合并幂等）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import LeadSignal

# 信号类型词表（前端证据卡展示用）
SIGNAL_TYPE_LABELS_ZH: dict[str, str] = {
    "whatsapp_link": "官网 WhatsApp 直链",
    "whatsapp_plugin": "官网 WhatsApp 插件",
    "whatsapp_number": "WhatsApp 号码",
    "whatsapp_group": "WhatsApp 群组（私域）",
    "wa_business": "WhatsApp Business",
    "fb_whatsapp": "FB 主页 WhatsApp 按钮",
    "meta_ad": "Meta 在投广告",
    "overseas_currency": "海外货币",
    "multilang": "多语言版本",
    "ecommerce_stack": "电商平台",
    "intl_shipping": "海外配送",
    "market_mention": "海外市场提及",
    "export_word": "出海自述",
    "job_signal": "招聘信号",
}


async def upsert_signal(
    db: AsyncSession,
    lead_id: int,
    signal_type: str,
    value: str,
    *,
    source: str = "",
    evidence_url: str | None = None,
    evidence_raw: str | None = None,
    confidence: int = 80,
) -> bool:
    """写入一条信号证据。返回是否新建（False = 已存在仅刷新 last_seen）。"""
    if not value:
        return False
    value = str(value)[:512]
    row = (
        await db.execute(
            select(LeadSignal).where(
                LeadSignal.lead_id == lead_id,
                LeadSignal.signal_type == signal_type,
                LeadSignal.value == value,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        from datetime import datetime, timezone

        row.last_seen = datetime.now(timezone.utc)
        # 证据更具体时补强（新来源带了 URL/原文而旧行没有）
        if evidence_url and not row.evidence_url:
            row.evidence_url = evidence_url[:512]
        if evidence_raw and not row.evidence_raw:
            row.evidence_raw = str(evidence_raw)[:1024]
        if confidence > row.confidence:
            row.confidence = confidence
        return False
    db.add(
        LeadSignal(
            lead_id=lead_id,
            signal_type=signal_type,
            value=value,
            source=source or "",
            evidence_url=(evidence_url[:512] if evidence_url else None),
            evidence_raw=(str(evidence_raw)[:1024] if evidence_raw else None),
            confidence=confidence,
        )
    )
    # 全局 sessionmaker autoflush=False：不显式 flush 的话，同会话里第二次
    # upsert 同一 (type, value) 的 SELECT 看不到挂起行 → 重复 add → commit
    # 撞唯一约束（website_enrich 一页多号码/多页同号码都会触发）
    await db.flush()
    return True


async def list_signals(db: AsyncSession, lead_id: int, limit: int = 200) -> list[LeadSignal]:
    """线索证据链（详情页证据卡数据源）。"""
    rows = (
        await db.execute(
            select(LeadSignal)
            .where(LeadSignal.lead_id == lead_id)
            .order_by(LeadSignal.confidence.desc(), LeadSignal.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)
