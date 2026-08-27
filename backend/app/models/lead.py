"""线索 ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_class import Base, TimestampMixin


class Lead(Base, TimestampMixin):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)

    # ---------- 基础属性 ----------
    country: Mapped[str | None] = mapped_column(String(8), index=True)  # ISO2，如 MY / PH
    city: Mapped[str | None] = mapped_column(String(128))
    industry: Mapped[str | None] = mapped_column(String(128))
    address: Mapped[str | None] = mapped_column(String(512))
    phone_raw: Mapped[str | None] = mapped_column(String(64))  # 采集原始电话
    phone_e164: Mapped[str | None] = mapped_column(String(32), index=True)  # 归一化 E.164
    website: Mapped[str | None] = mapped_column(String(512))
    domain: Mapped[str | None] = mapped_column(String(255), index=True)  # registrable domain

    # ---------- 富化结果 ----------
    email: Mapped[str | None] = mapped_column(String(255))
    social: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # {platform: url}
    whatsapp_hit: Mapped[bool] = mapped_column(Boolean, default=False)  # 官网有 WhatsApp 插件/链接
    whatsapp_url: Mapped[str | None] = mapped_column(String(512))
    whatsapp_job: Mapped[bool] = mapped_column(Boolean, default=False)  # 在招 WhatsApp 相关岗位
    job_urls: Mapped[list[str]] = mapped_column(JSON, default=list)  # 在招岗位帖 URL
    enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )  # 上次成功富化时间

    # ---------- 去重与评分 ----------
    # 主身份键，优先级：domain > phone_e164 > md5(归一化名称+城市)
    dedupe_key: Mapped[str] = mapped_column(String(191), unique=True, index=True)
    # 名称维度身份键（md5(归一化名称+城市)）：跨来源合并的反查列。
    # 行的主键可能是 domain:/tel:，另一来源只有名称+城市进来时靠这列命中。
    namecity_key: Mapped[str | None] = mapped_column(String(64), index=True)
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    score_signals: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)  # {信号键: 得分}

    # ---------- 来源记录：[{source, first_seen, last_seen}]，按 (lead, source) 唯一 ----------
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    def __repr__(self) -> str:
        return f"<Lead id={self.id} name={self.name!r} score={self.score}>"
