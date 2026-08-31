"""联系人 CRUD：seniority 自动分层 + 邮箱自动生成 + 评分联动。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError
from app.crud.lead_events import add_event, rescore_and_log
from app.models.lead import Lead, LeadContact

# ---------- seniority 分层关键词（job_title 命中即分层，优先级 tier1 > tier2 > tier3） ----------

_TIER_KEYWORDS: list[tuple[str, list[str]]] = [
    (
        "tier1",
        [
            "ceo", "founder", "co-founder", "cofounder", "owner", "president",
            "managing director", "general manager",
            "董事长", "创始人", "总经理", "执行董事",
        ],
    ),
    (
        "tier2",
        [
            "cmo", "chief marketing", "marketing director", "marketing manager",
            "head of marketing", "growth", "customer service", "customer support",
            "customer success", "crm",
            "市场总监", "营销总监", "市场负责人", "客服负责人", "客服经理", "客服主管", "运营总监",
        ],
    ),
    (
        "tier3",
        [
            "cto", "cio", "chief technology", "it manager", "it director",
            "product manager", "tech lead",
            "技术总监", "IT主管", "产品经理",
        ],
    ),
]


def derive_seniority(job_title: str | None) -> str | None:
    """job_title → tier1/tier2/tier3/unknown；无 job_title 返回 None（未分层）。"""
    if not job_title or not job_title.strip():
        return None
    title = job_title.lower()
    for tier, keywords in _TIER_KEYWORDS:
        if any(kw in title for kw in keywords):
            return tier
    return "unknown"


async def get_contact(db: AsyncSession, lead_id: int, contact_id: int) -> LeadContact | None:
    return (
        await db.execute(
            select(LeadContact).where(
                LeadContact.id == contact_id, LeadContact.lead_id == lead_id
            )
        )
    ).scalar_one_or_none()


async def list_contacts(db: AsyncSession, lead_id: int) -> list[LeadContact]:
    rows = await db.execute(
        select(LeadContact).where(LeadContact.lead_id == lead_id).order_by(LeadContact.id)
    )
    return list(rows.scalars().all())


async def _find_by_email(db: AsyncSession, lead_id: int, email: str) -> LeadContact | None:
    return (
        await db.execute(
            select(LeadContact).where(
                LeadContact.lead_id == lead_id, LeadContact.email == email
            )
        )
    ).scalar_one_or_none()


async def create_contact(
    db: AsyncSession,
    lead: Lead,
    payload: Any,  # schemas.ContactCreate（避免循环导入用鸭子类型）
    *,
    source: str = "manual",
    created_by: int | None = None,
) -> LeadContact:
    """手工新增联系人：email 同 lead 内唯一；新增后重评（联系人维度）。"""
    if payload.email:
        dup = await _find_by_email(db, lead.id, payload.email)
        if dup is not None:
            raise BusinessError(40001, message="该邮箱的联系人已存在")
    contact = LeadContact(
        lead_id=lead.id,
        name=payload.name,
        job_title=payload.job_title,
        department=payload.department,
        email=payload.email,
        phone=payload.phone,
        linkedin=payload.linkedin,
        seniority=derive_seniority(payload.job_title),
        confidence=payload.confidence if payload.confidence is not None else 50,
        source=source,
    )
    db.add(contact)
    await db.flush()
    add_event(
        db,
        lead,
        "contact_added",
        payload={"contact_id": contact.id, "email": contact.email, "source": source},
        note=f"新增联系人：{contact.name or contact.email or contact.job_title or f'#{contact.id}'}",
        created_by=created_by,
    )
    await rescore_and_log(db, lead, created_by=created_by)
    return contact


async def update_contact(
    db: AsyncSession,
    lead: Lead,
    contact: LeadContact,
    payload: Any,  # schemas.ContactUpdate
    *,
    created_by: int | None = None,
) -> LeadContact:
    """编辑联系人：email 改动查重；job_title 变了重新分层；重评。"""
    data = payload.model_dump(exclude_unset=True)
    new_email = data.get("email")
    if new_email and new_email != contact.email:
        dup = await _find_by_email(db, lead.id, new_email)
        if dup is not None and dup.id != contact.id:
            raise BusinessError(40001, message="该邮箱的联系人已存在")
    for field in ("name", "job_title", "department", "email", "phone", "linkedin"):
        if field in data:
            setattr(contact, field, data[field])
    if "confidence" in data and data["confidence"] is not None:
        contact.confidence = data["confidence"]
    contact.seniority = derive_seniority(contact.job_title)
    await db.flush()
    await rescore_and_log(db, lead, created_by=created_by)
    return contact


async def delete_contact(db: AsyncSession, lead: Lead, contact: LeadContact) -> None:
    """删除联系人后重评（联系人维度可能降分）。"""
    await db.delete(contact)
    await db.flush()
    await rescore_and_log(db, lead)


async def auto_create_from_email(
    db: AsyncSession,
    lead: Lead,
    email: str,
    *,
    source: str = "website_enrich",
) -> LeadContact | None:
    """富化/采集抓到公开邮箱 → 自动生成「待补全」联系人（job_title 空，前端显示待补全）。

    已存在同邮箱联系人时返回 None。只建记录 + 事件，不重评——调用方
    （website_enrich._enrich_one）在富化末尾统一 rescore_and_log。
    source 透传（2026-08-31 审计：此前硬编码 website_enrich，meta_ads 抓的
    邮箱建的联系人来源被误标）。
    """
    if not email:
        return None
    dup = await _find_by_email(db, lead.id, email)
    if dup is not None:
        return None
    contact = LeadContact(
        lead_id=lead.id,
        name=None,
        job_title=None,
        email=email,
        seniority=None,
        confidence=40,
        source=source,
    )
    db.add(contact)
    await db.flush()
    add_event(
        db,
        lead,
        "contact_added",
        payload={"contact_id": contact.id, "email": email, "source": source},
        note=f"采集发现公开邮箱，自动生成联系人：{email}",
    )
    return contact


async def auto_create_from_phone(
    db: AsyncSession,
    lead: Lead,
    phone: str,
    *,
    source: str = "website_enrich",
    is_wa: bool = True,
) -> LeadContact | None:
    """WhatsApp 号码/tel 电话 → 自动联系人（「找谁」的直接答案）。

    同 lead 内同号码已存在则不重复建。号码是 WhatsApp 入口时建联对象就是
    这个号——name 待补全，phone 存号码（补 + 存国际格式）。
    is_wa 显式传（2026-08-31 审计：此前按 source 名猜置信度，meta_ads 主页
    抠的 wa.me 号码被错标 60——两个调用方的号码都来自 wa.me 链接，默认 85）。
    """
    if not phone:
        return None
    number = phone.lstrip("+")
    if not number.isdigit():
        return None
    phone_val = f"+{number}" if not phone.startswith("+") else phone
    dup = (
        await db.execute(
            select(LeadContact).where(
                LeadContact.lead_id == lead.id, LeadContact.phone == phone_val
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        return None
    contact = LeadContact(
        lead_id=lead.id,
        name=None,
        job_title=None,
        phone=phone_val,
        seniority=None,
        confidence=85 if is_wa else 60,
        source=source,
    )
    db.add(contact)
    await db.flush()
    add_event(
        db,
        lead,
        "contact_added",
        payload={"contact_id": contact.id, "phone": phone_val, "source": source},
        note=f"{'WhatsApp 号码' if is_wa else '电话号码'}，自动生成联系人：{phone_val}",
    )
    return contact
