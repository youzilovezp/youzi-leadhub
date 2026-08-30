"""线索 ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
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

    # ---------- 跟进（销售工作台） ----------
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )  # 跟进人（最后操作的跟进人）
    follow_status: Mapped[str | None] = mapped_column(String(16), index=True)  # 见 FOLLOW_STATUS_OPTIONS，NULL=从未跟进
    last_followed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_follow_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 下次回访时间

    # ---------- 中国出海 ICP（meta_ads 链路） ----------
    is_cn: Mapped[bool] = mapped_column(Boolean, default=False, index=True)  # 中国出海企业特征
    fb_whatsapp: Mapped[bool] = mapped_column(
        Boolean, default=False, index=True
    )  # FB 主页带 wa.me 按钮（CTWA/私域运营证据）

    def __repr__(self) -> str:
        return f"<Lead id={self.id} name={self.name!r} score={self.score}>"


class LeadFollowUp(Base):
    """跟进历史（一次跟进一条，不可变记录；弹窗里的时间线数据源）。"""

    __tablename__ = "lead_follow_ups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # SET NULL：销售离职删账号后历史仍保留，只是显示不出姓名
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # 本次跟进后的状态
    note: Mapped[str | None] = mapped_column(Text)
    next_follow_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
