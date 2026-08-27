"""线索 CRUD + 去重合并（upsert）。

合并语义（需求文档）：
    同一企业多来源进入 → 补空字段、追加来源记录（按 (lead, source) 唯一）、重算评分。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import LeadDraft
from app.collectors.normalize import (
    extract_domain,
    make_dedupe_key,
    normalize_company_name,
    normalize_phone,
)
from app.collectors.scoring import compute_score
from app.models.lead import Lead


def _namecity_key(name: str | None, city: str | None) -> str | None:
    """md5(归一化名称+城市)——供反查的名称维度身份键。"""
    norm = normalize_company_name(name)
    if not norm:
        return None
    return "namecity:" + hashlib.md5(f"{norm}|{(city or '').strip().lower()}".encode()).hexdigest()


async def upsert_lead(db: AsyncSession, draft: LeadDraft) -> tuple[Lead, bool]:
    """归一化 → dedupe_key → 新建或合并。返回 (lead, 是否新建)。

    并发安全：两个 worker 同时新建同一 dedupe_key 时，后 insert 的一方命中唯一约束。
    用 savepoint 包住 insert，IntegrityError 后重查存量并退化为合并（此时必命中）。
    """
    if not draft.name or not draft.name.strip():
        raise ValueError("lead name is required")

    region = (draft.country or "").upper() or None
    phone_e164 = normalize_phone(draft.phone_raw, region)
    domain = extract_domain(draft.website)
    dedupe_key = make_dedupe_key(
        website=draft.website,
        phone_raw=draft.phone_raw,
        phone_e164=phone_e164,
        name=draft.name,
        city=draft.city,
        region=region,
    )
    if dedupe_key is None:
        # 三键全空（名称只剩标点/法律后缀等极端情况）：无法去重，
        # 用 source+name 兜底（宁漏勿重——单独成条）
        dedupe_key = f"raw:{draft.name.strip().lower()}|{draft.source}"

    namecity_key = _namecity_key(draft.name, draft.city)
    conds = _identity_conds(dedupe_key, domain, phone_e164, namecity_key)

    existing = await _find_existing(db, conds)
    now = datetime.now(timezone.utc)

    if existing is None:
        lead = _new_lead(draft, dedupe_key, namecity_key, domain, phone_e164, now)
        from sqlalchemy.exc import IntegrityError

        try:
            async with db.begin_nested():  # savepoint：冲突只回滚这次 insert
                db.add(lead)
                await db.flush()
        except IntegrityError:
            existing = await _find_existing(db, conds)
            if existing is None:  # 冲突来自别的唯一列等意外——不吞
                raise
        else:
            return lead, True

    await _merge_into(db, existing, draft, domain, phone_e164, namecity_key, now)
    await db.flush()
    return existing, False


def _identity_conds(
    dedupe_key: str, domain: str | None, phone_e164: str | None, namecity_key: str | None
) -> list:
    """跨来源合并：不能只比对 draft 自己的主键（d1 可能存的是 tel: 键，d2 带
    domain 进来）。三个身份列（domain / phone_e164 / namecity_key）任一命中 → 合并。"""
    conds = [Lead.dedupe_key == dedupe_key]
    if domain:
        conds.append(Lead.domain == domain)
    if phone_e164:
        conds.append(Lead.phone_e164 == phone_e164)
    if namecity_key:
        conds.append(Lead.namecity_key == namecity_key)
    return conds


async def _find_existing(db: AsyncSession, conds: list) -> Lead | None:
    from sqlalchemy import or_

    return (
        await db.execute(select(Lead).where(or_(*conds)).order_by(Lead.id).limit(1))
    ).scalar_one_or_none()


def _new_lead(
    draft: LeadDraft,
    dedupe_key: str,
    namecity_key: str | None,
    domain: str | None,
    phone_e164: str | None,
    now: datetime,
) -> Lead:
    score, signals = compute_score(
        whatsapp_hit=bool(draft.whatsapp_url),
        whatsapp_job=draft.whatsapp_job,
        website=draft.website,
        email=draft.email,
        country=draft.country,
        phone_raw=draft.phone_raw,
        phone_e164=phone_e164,
        social=draft.social,
    )
    return Lead(
        name=draft.name.strip(),
        country=draft.country,
        city=draft.city,
        industry=draft.industry,
        address=draft.address,
        phone_raw=draft.phone_raw,
        phone_e164=phone_e164,
        website=draft.website,
        domain=domain,
        email=draft.email,
        social=dict(draft.social or {}),
        whatsapp_hit=bool(draft.whatsapp_url),
        whatsapp_url=draft.whatsapp_url,
        whatsapp_job=draft.whatsapp_job,
        job_urls=list(draft.job_urls or []),
        sources=[{"source": draft.source, "first_seen": now.isoformat(), "last_seen": now.isoformat()}],
        dedupe_key=dedupe_key,
        namecity_key=namecity_key,
        score=score,
        score_signals=signals,
    )


async def _merge_into(
    db: AsyncSession,
    existing: Lead,
    draft: LeadDraft,
    domain: str | None,
    phone_e164: str | None,
    namecity_key: str | None,
    now: datetime,
) -> None:
    """合并语义：标量补空、布尔 OR、URL/social 并集、来源 (lead, source) 唯一、重算评分。"""
    for field, value in (
        ("country", draft.country),
        ("city", draft.city),
        ("industry", draft.industry),
        ("address", draft.address),
        ("phone_raw", draft.phone_raw),
        ("website", draft.website),
        ("domain", domain),
        ("email", draft.email),
        ("whatsapp_url", draft.whatsapp_url),
    ):
        if value and not getattr(existing, field):
            setattr(existing, field, value)
    if phone_e164 and not existing.phone_e164:
        existing.phone_e164 = phone_e164
    if namecity_key and not existing.namecity_key:
        existing.namecity_key = namecity_key
    if draft.whatsapp_url:
        existing.whatsapp_hit = True
        if not existing.whatsapp_url:
            existing.whatsapp_url = draft.whatsapp_url
    if draft.whatsapp_job:
        existing.whatsapp_job = True
    if draft.job_urls:
        urls = list(existing.job_urls or [])
        for u in draft.job_urls:
            if u not in urls:
                urls.append(u)
        existing.job_urls = urls
    if draft.social:
        merged = dict(existing.social or {})
        for k, v in draft.social.items():
            merged.setdefault(k, v)
        existing.social = merged
    _touch_source(existing, draft.source, now)

    # 合并后键升级：优先 domain（新数据可能补上了官网），冲突则保留旧键
    upgraded = make_dedupe_key(
        website=existing.website,
        phone_raw=existing.phone_raw,
        phone_e164=existing.phone_e164,
        name=existing.name,
        city=existing.city,
    )
    if upgraded and upgraded != existing.dedupe_key:
        conflict = (
            await db.execute(
                select(Lead.id).where(Lead.dedupe_key == upgraded, Lead.id != existing.id)
            )
        ).scalar_one_or_none()
        if conflict is None:
            existing.dedupe_key = upgraded

    existing.score, existing.score_signals = _score_from_lead(existing)


def _score_from_lead(lead: Lead) -> tuple[int, dict[str, int]]:
    return compute_score(
        whatsapp_hit=lead.whatsapp_hit,
        whatsapp_job=lead.whatsapp_job,
        website=lead.website,
        email=lead.email,
        country=lead.country,
        phone_raw=lead.phone_raw,
        phone_e164=lead.phone_e164,
        social=lead.social,
    )


def _touch_source(lead: Lead, source: str, now: datetime) -> None:
    """来源记录按 (lead, source) 唯一：已有则刷 last_seen，没有才追加。"""
    records = list(lead.sources or [])
    for rec in records:
        if rec.get("source") == source:
            rec["last_seen"] = now.isoformat()
            lead.sources = records  # 重新赋值触发 JSON 变更追踪
            return
    records.append({"source": source, "first_seen": now.isoformat(), "last_seen": now.isoformat()})
    lead.sources = records


async def rescore_lead(db: AsyncSession, lead: Lead) -> Lead:
    """字段被 API 层改动后重算评分（手工编辑入口用）。"""
    lead.score, lead.score_signals = _score_from_lead(lead)
    await db.flush()
    return lead


async def search_leads(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    country: str | None = None,
    industry: str | None = None,
    source: str | None = None,
    min_score: int | None = None,
    whatsapp_hit: bool | None = None,
    has_website: bool | None = None,
    keyword: str | None = None,
) -> tuple[list[Lead], int]:
    """线索列表筛选：国家/行业/来源/评分下限/WhatsApp 检测/关键词。"""
    from sqlalchemy import func, or_

    stmt = select(Lead)
    count_stmt = select(func.count()).select_from(Lead)
    conds = []
    if country:
        conds.append(Lead.country == country.upper())
    if industry:
        conds.append(Lead.industry == industry)
    if min_score is not None:
        conds.append(Lead.score >= min_score)
    if whatsapp_hit is not None:
        conds.append(Lead.whatsapp_hit.is_(whatsapp_hit))
    if has_website is not None:
        if has_website:
            conds.append(Lead.website.is_not(None) & (Lead.website != ""))
        else:
            conds.append((Lead.website.is_(None)) | (Lead.website == ""))
    if source:
        # sources 是 JSON 数组，用 LIKE 匹配。序列化格式有两种：
        # Python json.dumps 默认带空格（"source": "x"），PG jsonb / 部分驱动是紧凑格式
        # （"source":"x"）——两种都匹配，否则 PG 下筛选直接失灵。
        src_text = Lead.sources.cast(func.text())
        conds.append(
            or_(src_text.contains(f'"source": "{source}"'),
                src_text.contains(f'"source":"{source}"'))
        )
    if keyword:
        like = f"%{keyword}%"
        conds.append(
            or_(
                Lead.name.ilike(like),
                Lead.email.ilike(like),
                Lead.domain.ilike(like),
                Lead.phone_e164.ilike(like),
                Lead.city.ilike(like),
            )
        )
    for cond in conds:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    total = (await db.execute(count_stmt)).scalar_one()
    stmt = stmt.order_by(Lead.score.desc(), Lead.id.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list((await db.execute(stmt)).scalars().all())
    return items, total
