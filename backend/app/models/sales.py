"""销售域 ORM：商机（轻量 CRM）+ 话术审核队列（V3 Agent 受控落地）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_class import Base, TimestampMixin


class Opportunity(Base, TimestampMixin):
    """商机（PRD §37 轻量 CRM：Lead → Contact → Opportunity → Deal）。

    stage 沿漏斗推进：opportunity 有效商机 → quote 报价 → negotiation 谈判
    → won 成交（记 won_at + amount 为成交金额）/ lost 失去。
    """

    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # 商机名称，如「WA 客服 SaaS 年单」
    amount: Mapped[int] = mapped_column(Integer, default=0)  # 金额（元，整数）
    stage: Mapped[str] = mapped_column(String(16), default="opportunity", index=True)
    expected_close_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    won_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # SET NULL：销售离职删账号后商机仍保留
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    note: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<Opportunity id={self.id} lead_id={self.lead_id} stage={self.stage} amount={self.amount}>"


class SalesMessage(Base, TimestampMixin):
    """话术审核队列（PRD §56：生成 → 销售审核 → 发送 → 记录）。

    V1 发送 = 销售复制内容手动发出后点「标记已发」（不直接调 WA API 自动外发，
    留人工把关；接 BSP 凭据后可在此表上扩自动通道）。
    """

    __tablename__ = "sales_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), default="whatsapp")  # whatsapp / email
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)  # draft/approved/rejected/sent
    generated_by: Mapped[str] = mapped_column(String(16), default="llm")  # llm / template
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<SalesMessage id={self.id} lead_id={self.lead_id} status={self.status}>"
