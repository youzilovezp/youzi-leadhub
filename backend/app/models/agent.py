"""代理式销售运营 ORM 模型。

定位（PRD §代理式销售运营助理）：
    线索 → 富化（已存在）→ 起草个性化外联 → 多通道发送 → 跟踪回复 → 同步 CRM 状态
    → 更新预测（按阶段加权重计算加权金额）。

5 张表：
    OutreachSequence    序列模板（按场景/通道的多步节奏：T+0 首触 / T+3 跟进 / ...）
    OutreachMessage     单条外联（草稿/已发/已回复/已退信），关联 lead + contact + step
    Reply               客户回复（消息来源 + 情感 + 意向标签 + 摘要）
    ForecastDeal        商机预测基线（lead × 阶段 × 金额 × 概率 × 预计成交日）
    ForecastSnapshot    预测快照（按期：weighted_total / by_stage / by_owner 不可变追加）

不变量：
    - 草稿可编辑，approved 锁定字段（防销售手滑改已发邮件正文）
    - 状态机：draft → approved → sent → (replied | no_reply | bounced) → (won | lost)
    - 阶段概率参考（forecast_deals.PROBABILITY_BY_STAGE）：won=100 / quote=70 /
      negotiation=60 / opportunity=40 / contacted=15 / replied=25 / pending=5
    - 加权金额 = Σ amount × probability / 100（按快照不可变记录）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_class import Base, TimestampMixin


# ---------- 词表 / 常量（schema 与业务共用） ----------


# 外联通道（与现有 lead 模型社交/邮箱字段对齐）
OUTREACH_CHANNELS: tuple[str, ...] = ("email", "whatsapp", "linkedin", "sms")
OUTREACH_CHANNEL_LABELS_ZH: dict[str, str] = {
    "email": "邮件",
    "whatsapp": "WhatsApp",
    "linkedin": "LinkedIn",
    "sms": "短信",
}


# 外联消息状态机（必须按顺序流转；transition 由 crud/agent 校验）
OUTREACH_STATUSES: tuple[str, ...] = (
    "draft",  # 起草中（LLM 或人工）
    "approved",  # 已审批待发（字段锁定）
    "sent",  # 已发送（实际通道回执后）
    "replied",  # 收到回复（关联 reply_id）
    "no_reply",  # 跟进超时无回复
    "bounced",  # 退信/发送失败
    "won",  # 商机赢单（CRM 终态）
    "lost",  # 商机输单（CRM 终态）
)
# 只有 won / lost 是真正终态。replied/no_reply/bounced 是中间态：
#   replied → won/lost（CRM 转化推进）
#   no_reply → won/lost（销售判定）
#   bounced → draft（重写重发）/ lost（放弃）
OUTREACH_TERMINAL_STATUSES: frozenset[str] = frozenset({"won", "lost"})
OUTREACH_EDITABLE_STATUSES: frozenset[str] = frozenset({"draft"})


# 回复情感（中性+正负双向；-1/0/1 三档足够，避免 5 档在批量 LLM 标注里噪声大）
REPLY_SENTIMENTS: tuple[str, ...] = ("negative", "neutral", "positive")
REPLY_SENTIMENT_LABELS_ZH: dict[str, str] = {
    "negative": "负向",
    "neutral": "中性",
    "positive": "正向",
}


# 回复意向标签（用于自动更新 follow_status / 触发 won/lost）
REPLY_INTENTS: tuple[str, ...] = (
    "interested",  # 有兴趣（→ opportunity）
    "objection",  # 异议（→ contacted + note）
    "question",  # 提问（→ contacted）
    "unsubscribe",  # 退订（→ paused）
    "wrong_person",  # 非对接人（→ contacted + note）
    "out_of_office",  # 假期/自动回执（不更新状态）
    "buy_signal",  # 强购买信号（→ quote）
    "other",  # 其他
)
REPLY_INTENT_LABELS_ZH: dict[str, str] = {
    "interested": "有兴趣",
    "objection": "异议",
    "question": "提问",
    "unsubscribe": "退订",
    "wrong_person": "非对接人",
    "out_of_office": "外出/自动回执",
    "buy_signal": "购买信号",
    "other": "其他",
}


# 商机阶段（与现有 lead.follow_status 对齐；forecast_deals.stage 复用）
FORECAST_STAGES: tuple[str, ...] = (
    "pending",  # 待跟进
    "contacted",  # 已联系
    "replied",  # 已回复
    "opportunity",  # 有效商机
    "quote",  # 报价
    "negotiation",  # 谈判
    "won",  # 成交
    "lost",  # 输单
)
FORECAST_OPEN_STAGES: frozenset[str] = frozenset(
    {"pending", "contacted", "replied", "opportunity", "quote", "negotiation"}
)


# 阶段默认概率（百分比；快照加权计算直接引用）
PROBABILITY_BY_STAGE: dict[str, int] = {
    "pending": 5,
    "contacted": 15,
    "replied": 25,
    "opportunity": 40,
    "quote": 70,
    "negotiation": 60,
    "won": 100,
    "lost": 0,
}


# ---------- OutreachSequence 序列模板 ----------


class OutreachSequence(Base, TimestampMixin):
    """外联序列模板（按场景定义多步节奏）。

    steps_json 结构：
        [
            {"day_offset": 0, "channel": "email",    "template_hint": "首触·痛点开场"},
            {"day_offset": 3, "channel": "email",    "template_hint": "跟进·案例"},
            {"day_offset": 7, "channel": "whatsapp", "template_hint": "价值重申"},
            {"day_offset": 14,"channel": "email",    "template_hint": "收尾·破冰邮件"},
        ]
    销售/管理员在前端可视化编辑；后台按 day_offset 排程批量生成草稿。
    """

    __tablename__ = "outreach_sequences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(512))
    # 适用场景标签（首触/唤醒/续约/...），前端做下拉
    scenario: Mapped[str] = mapped_column(String(32), default="first_touch", index=True)
    channel: Mapped[str] = mapped_column(String(16), default="email")  # 主通道（用于模板筛选）
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # 所有者（null=系统共享；个人私有序列 = 创建人）
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    def __repr__(self) -> str:
        return f"<OutreachSequence id={self.id} {self.name!r} steps={len(self.steps or [])}>"


# ---------- OutreachMessage 单条外联 ----------


class OutreachMessage(Base, TimestampMixin):
    """单条外联：草稿/已发/已回复。

    与 lead.contact 多对一（可空：contact 缺失时给 lead 主邮箱）。
    与 outreach_sequences 可空关联（自由发送无模板时不挂序列）。
    """

    __tablename__ = "outreach_messages"
    __table_args__ = (
        # 同一线索同一序列同一 step 仅一条（防止并发重复排程）
        UniqueConstraint(
            "lead_id",
            "sequence_id",
            "step_index",
            name="uq_outreach_lead_seq_step",
        ),
        Index("ix_outreach_lead_status", "lead_id", "status"),
        Index("ix_outreach_owner_status", "owner_id", "status"),
        Index("ix_outreach_scheduled", "scheduled_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("lead_contacts.id", ondelete="SET NULL"), index=True
    )
    sequence_id: Mapped[int | None] = mapped_column(
        ForeignKey("outreach_sequences.id", ondelete="SET NULL"), index=True
    )
    step_index: Mapped[int | None] = mapped_column(Integer)  # 序列内步序号
    channel: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    subject: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    # LLM 参与标记（人工重写后保留 True 便于审计「是机器写的」）
    llm_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    generated_by: Mapped[str] = mapped_column(String(16), default="template")  # llm/template/manual

    # 通道执行：scheduled_at 排程 / sent_at 已发 / sent_error 失败原因
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_error: Mapped[str | None] = mapped_column(String(512))

    # 关联回复（replied 状态时填）
    reply_id: Mapped[int | None] = mapped_column(
        ForeignKey("replies.id", ondelete="SET NULL"), index=True
    )

    # 跟进人（默认 = lead.owner_id，写入时快照，销售离职不影响历史归属）
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # 锁字段（approved 后正文不可改，防止销售改已审批邮件为敏感词）
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    def __repr__(self) -> str:
        return f"<OutreachMessage id={self.id} lead={self.lead_id} {self.channel} {self.status}>"


# ---------- Reply 客户回复 ----------


class Reply(Base, TimestampMixin):
    """客户回复（邮件回信 / WhatsApp 回执 / 人工录入）。

    字段约束：
    - message_id 非空 = 自动关联到具体外联；空 = 自由来信（陌生咨询）
    - lead_id 非空（必须先找到 lead；找不到时落 inbound_unmatched 表或丢弃）
    - sentiment / intent 由 LLM 自动标注，可人工覆盖
    - processed_at 非空 = 已根据 intent 触发 CRM 状态更新（防重入）
    """

    __tablename__ = "replies"
    __table_args__ = (
        Index("ix_replies_lead_received", "lead_id", "received_at"),
        Index("ix_replies_unprocessed", "received_at", "processed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("outreach_messages.id", ondelete="SET NULL"), index=True
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    from_address: Mapped[str | None] = mapped_column(String(255), index=True)  # 邮箱/号码
    subject: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sentiment: Mapped[str | None] = mapped_column(String(16), index=True)  # negative/neutral/positive
    intent: Mapped[str | None] = mapped_column(String(32), index=True)  # REPLY_INTENTS
    summary: Mapped[str | None] = mapped_column(String(512))  # LLM 一句话摘要
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    # 处理完成（CRM 状态已更新/通知已发/记录已写）；空 = 待处理
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # CRM 更新结果（成功/失败/跳过原因）
    crm_action: Mapped[str | None] = mapped_column(String(512))
    # 由 LLM 自动标注 True；人工覆盖后保持 True（审计）
    llm_labeled: Mapped[bool] = mapped_column(Boolean, default=False)
    # 标签覆盖标记（人工改了 sentiment/intent 时为 True）
    overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    # 处理人（自动处理 = 系统；人工 = 当前用户）
    handled_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    def __repr__(self) -> str:
        return f"<Reply id={self.id} lead={self.lead_id} intent={self.intent}>"


# ---------- ForecastDeal 商机预测基线 ----------


class ForecastDeal(Base, TimestampMixin):
    """商机预测基线（一条线索×一阶段=一行；同一 lead 多阶段时复制多行）。

    与 lead.follow_status 区别：
    - follow_status 是「销售主观判定」；forecast_deals 是「金额×概率可量化预测」
    - 同一 lead 可在多个 stage 留痕（如主商机 quote + 副商机 opportunity 单独金额）
    - 调整 stage/amount/probability 不会改 lead.follow_status，需手动同步
    """

    __tablename__ = "forecast_deals"
    __table_args__ = (
        Index("ix_forecast_stage_close", "stage", "close_date"),
        Index("ix_forecast_owner_stage", "owner_id", "stage"),
        # 同一 lead 同时只允许一条「主商机」（is_primary=True）；同 lead 副商机不限
        # ponytail: 没有 DB 层 unique——应用层保证；接受风险以避免阻塞销售多商机场景
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # 商机名（如「WA API 年付」）
    stage: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Float, default=0)  # 商机金额（主币种：人民币）
    probability: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    close_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    def __repr__(self) -> str:
        return f"<ForecastDeal id={self.id} lead={self.lead_id} {self.stage} ¥{self.amount:.0f}>"


# ---------- ForecastSnapshot 预测快照（不可变追加） ----------


class ForecastSnapshot(Base):
    """预测快照（周期维度：每周/每月 cron 跑一次追加）。

    data_json 结构：
        {
          "weighted_total": 1234500.0,
          "by_stage": {"quote": 200000, "negotiation": 350000, ...},
          "by_owner": {3: 80000, 7: 120000, ...},
          "deal_count": 42,
          "open_deal_count": 35,
          "computed_at": "...",
        }
    """

    __tablename__ = "forecast_snapshots"
    __table_args__ = (
        UniqueConstraint("period", "period_start", name="uq_forecast_period_start"),
        Index("ix_forecast_period_start", "period", "period_start"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period: Mapped[str] = mapped_column(String(16), nullable=False)  # weekly / monthly
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<ForecastSnapshot id={self.id} {self.period} "
            f"{self.period_start.date()} weighted={self.data.get('weighted_total', 0):.0f}>"
        )
