"""线索 ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_class import Base, TimestampMixin


class Lead(Base, TimestampMixin):
    __tablename__ = "leads"
    # domain 去重不变量下沉 DB（迁移 f7c2a91d4e21）：部分唯一索引，
    # NULL 不受约束（多行无域正常）。并发双写同域 → IntegrityError，
    # upsert/发现链按 savepoint 兜底退化为合并/负缓存
    __table_args__ = (
        Index(
            "uq_leads_domain",
            "domain",
            unique=True,
            sqlite_where=text("domain IS NOT NULL"),
            postgresql_where=text("domain IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)

    # ---------- 基础属性 ----------
    country: Mapped[str | None] = mapped_column(String(8), index=True)  # ISO2，如 MY / PH
    city: Mapped[str | None] = mapped_column(String(128))
    industry: Mapped[str | None] = mapped_column(String(128), index=True)  # 列表/自动分配筛选用
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
    score_signals: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)  # {维度键: 维度分}（六维）
    # MVP 加分制明细（PRD §五 13 条）：{"total": 参考总分, "items": [{key,label,points}]}
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    grade: Mapped[str] = mapped_column(String(2), default="C", server_default="C", index=True)  # S/A/B/C

    # ---------- WhatsApp 场景 & SaaS 需求（website_enrich 检测，只增不减） ----------
    # scenes: customer_service / marketing / transactional / saas（见 collectors/scenes.py）
    scenes: Mapped[list[str]] = mapped_column(JSON, default=list)
    # saas_signals: {crm/helpdesk/chatbot/ai_service/marketing_automation/omnichannel: 命中关键词数}
    saas_signals: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    # 页面出现的全部 WhatsApp 号码（去重；多分线 = 规模化私域证据，§4.1）
    whatsapp_numbers: Mapped[list[str]] = mapped_column(JSON, default=list)
    # WhatsApp Business 使用（§4.1「号码类型/入口形态」代理判定：页面自述业务号）
    wa_business: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # ---------- 来源记录：[{source, first_seen, last_seen}]，按 (lead, source) 唯一 ----------
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    # ---------- 跟进（销售工作台） ----------
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )  # 跟进人（最后操作的跟进人）
    follow_status: Mapped[str | None] = mapped_column(String(16), index=True)  # 见 FOLLOW_STATUS_OPTIONS，NULL=从未跟进
    last_followed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_follow_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )  # 下次回访时间（「该回访了」筛选走索引）

    # ---------- 中国出海 ICP（meta_ads 链路） ----------
    is_cn: Mapped[bool] = mapped_column(Boolean, default=False, index=True)  # 中国出海企业特征
    fb_whatsapp: Mapped[bool] = mapped_column(
        Boolean, default=False, index=True
    )  # FB 主页带 wa.me 按钮（CTWA/私域运营证据）

    # ---------- 出海画像（PRD §8：显式投放市场，meta_ads 累计） ----------
    target_countries: Mapped[list[str]] = mapped_column(JSON, default=list)  # 投放/目标国家 ISO2 列表
    export_type: Mapped[str | None] = mapped_column(String(64))  # 出海业务类型（如 跨境电商/品牌出海）

    # ---------- 出海信号（PRD §4.2，website_enrich 检测，只增不减） ----------
    # {currencies/languages/ecommerce/shipping/markets/export_words: [证据串]}
    overseas_signals: Mapped[dict[str, list[str]]] = mapped_column(JSON, default=dict)

    # ---------- 招聘信号细分（PRD §4.3，job_posting 按岗位标题分类） ----------
    # {wa_ops/overseas_cs/social_ops/crm_ops/overseas_sales: {label, points}}
    job_signals: Mapped[dict[str, dict[str, Any]]] = mapped_column(JSON, default=dict)

    # ---------- 广告信号（PRD §4.1 meta_ads 累计；CTWA 由 fb_whatsapp 代理） ----------
    ad_count: Mapped[int] = mapped_column(Integer, default=0)  # 累计在投广告数（只增）
    last_ad_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 最近一次见到在投广告

    # ---------- 字段级数据质量（PRD §32）：{字段: {source, updated_at, confidence}} ----------
    field_meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # ---------- ICP 资格（二重门：中国企业 × 出海业务，见 collectors/icp.py） ----------
    # qualified=CN+出海（销售池） / cn_domestic=CN 未出海（培育） /
    # foreign=有评估结论的非 CN（不进默认销售池） / unknown=证据不足（待验证）
    icp_status: Mapped[str] = mapped_column(
        String(16), default="unknown", server_default="unknown", index=True
    )

    def __repr__(self) -> str:
        return f"<Lead id={self.id} name={self.name!r} score={self.score} grade={self.grade}>"


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


class LeadContact(Base, TimestampMixin):
    """联系人（销售工作台数据）。

    来源：手工录入（manual）或 website_enrich 抓到公开邮箱自动生成
    （job_title 为空，前端展示「待补全」）。seniority 由 job_title 关键词自动分层，
    参与联系人质量维度评分。
    """

    __tablename__ = "lead_contacts"
    __table_args__ = (
        UniqueConstraint("lead_id", "email", name="uq_lead_contacts_lead_email"),
        # email 为 NULL 的多条记录在 PG/SQLite 都不受唯一约束限制（NULL != NULL）
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str | None] = mapped_column(String(255))
    job_title: Mapped[str | None] = mapped_column(String(128))
    department: Mapped[str | None] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    phone: Mapped[str | None] = mapped_column(String(64))
    linkedin: Mapped[str | None] = mapped_column(String(512))
    # tier1=CEO/Founder/GM 等决策层 tier2=Marketing/客服负责人 tier3=CTO/IT/PM unknown=未识别
    seniority: Mapped[str | None] = mapped_column(String(8), index=True)
    confidence: Mapped[int] = mapped_column(Integer, default=50)  # 0-100，手工录入默认 50
    source: Mapped[str] = mapped_column(String(32), default="manual")  # manual / website_enrich

    def __repr__(self) -> str:
        return f"<LeadContact id={self.id} lead_id={self.lead_id} email={self.email!r}>"


class LeadSignal(Base):
    """信号级证据链（PRD §4.1：每个信号保存 类型/值/来源页面/原文/发现时间/置信度）。

    写入方（collectors 检测命中时 upsert，(lead_id, signal_type, value) 唯一）：
    - website_enrich：whatsapp_link / whatsapp_plugin / whatsapp_number /
      overseas_currency / multilang / ecommerce_stack / intl_shipping / market_mention
    - meta_ads：fb_whatsapp / whatsapp_number / meta_ad

    与宽表 JSON 列（whatsapp_numbers/overseas_signals…）的关系：宽表是评分与
    筛选的快速输入，本表是可追溯证据——销售能看到"系统为什么判定有需求"。
    """

    __tablename__ = "lead_signals"
    __table_args__ = (
        UniqueConstraint("lead_id", "signal_type", "value", name="uq_lead_signals_type_value"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    signal_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)  # 见各 collector 词表
    value: Mapped[str] = mapped_column(String(512), nullable=False)  # 号码/货币码/平台名等
    evidence_url: Mapped[str | None] = mapped_column(String(512))  # 发现信号的页面
    evidence_raw: Mapped[str | None] = mapped_column(String(1024))  # 命中的链接/片段原文
    confidence: Mapped[int] = mapped_column(Integer, default=80)  # 0-100
    source: Mapped[str] = mapped_column(String(32), default="")  # website_enrich / meta_ads / job_posting
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<LeadSignal id={self.id} lead_id={self.lead_id} {self.signal_type}={self.value!r}>"


class LeadReview(Base):
    """人工抽检标注（§十二 验证闭环，2026-08-31）。

    度量三个准确率指标的原始数据（每条 = 某人某时对某线索某维度打的判定）：
    - whatsapp：whatsapp_hit/号码证据是否真实有效（目标 ≥90%）
    - overseas：qualified 判定是否属实（企业确实在做海外业务，目标 ≥80%）
    - contact：联系人邮箱/电话是否有效可达（目标 ≥60%）
    同一 (lead_id, field) 可多次标注（复审），统计取每人最新一条。
    """

    __tablename__ = "lead_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    field: Mapped[str] = mapped_column(String(16), index=True, nullable=False)  # whatsapp/overseas/contact
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)  # correct/incorrect/unsure
    note: Mapped[str | None] = mapped_column(String(512))
    # SET NULL：标注人账号删除后记录保留
    reviewer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        return f"<LeadReview id={self.id} lead_id={self.lead_id} {self.field}={self.verdict}>"


class LeadEvent(Base):
    """线索动态事件（不可变追加；详情页时间线数据源之一）。

    事件在三个变更点发射：upsert 新建/合并、website_enrich 富化、联系人 CRUD，
    统一走 crud/lead_events.rescore_and_log / diff_lead_events。
    """

    __tablename__ = "lead_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)  # 见 EVENT_TYPES
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # 事件明细（old/new 等）
    is_alert: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), index=True
    )  # 高价值预警（等级升到 S/A、发现 WhatsApp、SaaS 信号）——预警中心数据源
    note: Mapped[str | None] = mapped_column(Text)  # 时间线展示用一句话
    # SET NULL：操作人账号删除后事件仍保留
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
