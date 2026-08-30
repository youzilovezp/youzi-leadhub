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
from app.collectors.scoring import apply_score
from app.crud.lead_events import rescore_and_log, snapshot_lead
from app.models.lead import Lead, LeadEvent


def _namecity_key(name: str | None, city: str | None) -> str | None:
    """md5(归一化名称+城市)——供反查的名称维度身份键。"""
    norm = normalize_company_name(name)
    if not norm:
        return None
    return "namecity:" + hashlib.md5(f"{norm}|{(city or '').strip().lower()}".encode()).hexdigest()


def touch_field_meta(
    lead: Lead,
    field: str,
    source: str,
    *,
    confidence: int = 90,
    now: datetime | None = None,
) -> None:
    """字段级数据质量（PRD §32）：记录 {字段: {source, updated_at, confidence}}。

    JSON 列必须整体重新赋值才触发变更追踪。
    """
    meta = dict(lead.field_meta or {})
    meta[field] = {
        "source": source,
        "updated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "confidence": confidence,
    }
    lead.field_meta = meta


# ---------- 分配（PRD §24：手动分配/转移/释放 + 自动分配） ----------


async def assign_lead(
    db: AsyncSession,
    lead: Lead,
    owner_id: int,
    *,
    assigned_by: int | None = None,
) -> Lead:
    """指派/转移跟进人（撞单锁定 §44：分配后其他销售只读）。"""
    from app.crud.lead_events import add_event
    from app.models.user import User

    if await db.get(User, owner_id) is None:
        raise ValueError(f"跟进人不存在：{owner_id}")
    old = lead.owner_id
    lead.owner_id = owner_id
    # 共享池线索被认领后进入「待跟进」；已推进的状态不回退
    if lead.follow_status in (None, "unassigned"):
        lead.follow_status = "pending"
    if old != owner_id:
        add_event(
            db,
            lead,
            "assigned",
            payload={"old": old, "new": owner_id},
            note=f"分配跟进人 #{owner_id}" + (f"（原 #{old}）" if old else ""),
            created_by=assigned_by,
        )
    await db.flush()
    return lead


async def release_lead(db: AsyncSession, lead: Lead, *, released_by: int | None = None) -> Lead:
    """释放回共享池（主管可释放/重新分配 §44）。"""
    from app.crud.lead_events import add_event

    old = lead.owner_id
    lead.owner_id = None
    lead.follow_status = "unassigned"
    add_event(
        db,
        lead,
        "assigned",
        payload={"old": old, "new": None},
        note=f"释放回共享池（原 #{old}）" if old else "释放回共享池",
        created_by=released_by,
    )
    await db.flush()
    return lead


async def auto_assign_leads(
    db: AsyncSession,
    *,
    candidate_owner_ids: list[int],
    max_per_owner: int = 50,
    grade: str | None = None,
    min_score: int | None = None,
    industry: str | None = None,
    country: str | None = None,
    limit: int = 100,
) -> tuple[list[Lead], dict[int, int]]:
    """自动分配（§24）：把共享池线索按当前负载轮转分给候选销售。

    规则：只分未分配（NULL/unassigned）线索，按 score 降序；每个销售已有
    在跟进量 + 本次分得量 ≤ max_per_owner；返回 (分配的线索, 每人分得计数)。
    """
    if not candidate_owner_ids:
        return [], {}

    from sqlalchemy import func

    # 候选人当前负载
    rows = (
        await db.execute(
            select(Lead.owner_id, func.count())
            .where(Lead.owner_id.in_(candidate_owner_ids))
            .group_by(Lead.owner_id)
        )
    ).all()
    load = {r[0]: r[1] for r in rows}
    capacity = {uid: max_per_owner - load.get(uid, 0) for uid in candidate_owner_ids}

    conds: list = [
        Lead.owner_id.is_(None),  # 未分配 = owner IS NULL
        (Lead.follow_status.is_(None)) | (Lead.follow_status == "unassigned"),
    ]
    if grade:
        conds.append(Lead.grade == grade.upper())
    if min_score is not None:
        conds.append(Lead.score >= min_score)
    if industry:
        conds.append(Lead.industry == industry)
    if country:
        conds.append(Lead.country == country.upper())

    stmt = select(Lead).where(*conds).order_by(Lead.score.desc(), Lead.id.desc()).limit(limit)
    pool = list((await db.execute(stmt)).scalars().all())

    assigned: list[Lead] = []
    counts: dict[int, int] = {uid: 0 for uid in candidate_owner_ids}
    for lead in pool:
        # 轮转：取剩余容量最大的候选（并列取 id 小的，稳定）
        uid = min(
            (u for u in candidate_owner_ids if capacity.get(u, 0) > 0),
            key=lambda u: (-(capacity.get(u, 0)), u),
            default=None,
        )
        if uid is None:
            break
        await assign_lead(db, lead, uid)
        capacity[uid] -= 1
        counts[uid] += 1
        assigned.append(lead)
    return assigned, counts


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
            # 新建事件在 savepoint 成功后追加，避免 IntegrityError 重试路径残留
            db.add(
                LeadEvent(
                    lead_id=lead.id,
                    event_type="manual_entry" if draft.source == "manual" else "source_added",
                    payload={"source": draft.source},
                    note=f"{'手工录入创建' if draft.source == 'manual' else '新来源采集'}：{draft.source}",
                )
            )
            await db.flush()
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
    lead = Lead(
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
        is_cn=draft.is_cn,
        fb_whatsapp=draft.fb_whatsapp,
        target_countries=list(draft.target_countries or []),
        whatsapp_numbers=list(draft.whatsapp_numbers or []),
        wa_business=bool(draft.wa_business),
        overseas_signals=dict(draft.overseas_signals or {}),
        job_signals=dict(draft.job_signals or {}),
        ad_count=draft.ad_count or 0,
        sources=[{"source": draft.source, "first_seen": now.isoformat(), "last_seen": now.isoformat()}],
        dedupe_key=dedupe_key,
        namecity_key=namecity_key,
    )
    # 字段级数据质量（§32）：新建时所有非空字段标记来源
    for field, value in (
        ("website", draft.website),
        ("email", draft.email),
        ("phone_e164", phone_e164),
        ("whatsapp_url", draft.whatsapp_url),
        ("social", draft.social),
    ):
        if value:
            touch_field_meta(lead, field, draft.source, confidence=95, now=now)
    if draft.target_countries:
        touch_field_meta(lead, "target_countries", draft.source, confidence=90, now=now)
    if draft.whatsapp_numbers:
        touch_field_meta(lead, "whatsapp_numbers", draft.source, confidence=95, now=now)
    if draft.overseas_signals:
        touch_field_meta(lead, "overseas_signals", draft.source, confidence=85, now=now)
    if draft.job_signals:
        touch_field_meta(lead, "job_signals", draft.source, confidence=85, now=now)
    apply_score(lead)  # 写 score / score_signals（六维） / grade
    return lead


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
    before = snapshot_lead(existing)
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
    if draft.is_cn:
        existing.is_cn = True  # 布尔 OR：任一来源命中即认为是中国出海特征
    if draft.fb_whatsapp:
        existing.fb_whatsapp = True
    if draft.wa_business:
        existing.wa_business = True
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
        touch_field_meta(existing, "social", draft.source, confidence=95, now=now)
    if draft.target_countries:
        merged_countries = list(existing.target_countries or [])
        for c in draft.target_countries:
            if c not in merged_countries:
                merged_countries.append(c)
        existing.target_countries = merged_countries
        touch_field_meta(existing, "target_countries", draft.source, confidence=90, now=now)
    if draft.whatsapp_numbers:
        merged_wa = list(existing.whatsapp_numbers or [])
        for n in draft.whatsapp_numbers:
            if n not in merged_wa:
                merged_wa.append(n)
        existing.whatsapp_numbers = merged_wa
        touch_field_meta(existing, "whatsapp_numbers", draft.source, confidence=95, now=now)
    if draft.overseas_signals:
        # 出海信号只增不减：{键: [证据串]} 按键并集
        merged_ov = dict(existing.overseas_signals or {})
        for k, vals in draft.overseas_signals.items():
            bucket = list(merged_ov.get(k) or [])
            for v in vals or []:
                if v not in bucket:
                    bucket.append(v)
            merged_ov[k] = bucket
        existing.overseas_signals = merged_ov
        touch_field_meta(existing, "overseas_signals", draft.source, confidence=85, now=now)
    if draft.job_signals:
        # 招聘信号并集（同键取 points 大者；不同键累加）
        merged_js = dict(existing.job_signals or {})
        for k, v in draft.job_signals.items():
            old = merged_js.get(k)
            if old is None or int(v.get("points", 0)) > int(old.get("points", 0)):
                merged_js[k] = v
        existing.job_signals = merged_js
        touch_field_meta(existing, "job_signals", draft.source, confidence=85, now=now)
    if draft.ad_count:
        # 广告累计只增不减
        existing.ad_count = max(existing.ad_count or 0, draft.ad_count)
    # 补空成功的字段记数据质量来源（§32）
    for field, value in (
        ("website", draft.website),
        ("email", draft.email),
        ("phone_e164", phone_e164),
        ("whatsapp_url", draft.whatsapp_url),
    ):
        if value and not (existing.field_meta or {}).get(field):
            touch_field_meta(existing, field, draft.source, confidence=95, now=now)
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

    # 统一重评钩子：六维重算 + grade 写回 + 快照 diff 发射动态事件
    await rescore_and_log(db, existing, before=before)


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
    await rescore_and_log(db, lead)
    return lead


def _lead_conditions(
    *,
    country: str | None = None,
    industry: str | None = None,
    source: str | None = None,
    min_score: int | None = None,
    grade: str | None = None,
    whatsapp_hit: bool | None = None,
    has_website: bool | None = None,
    keyword: str | None = None,
    follow_status: str | None = None,
    owner_id: int | None = None,
    due_follow: bool | None = None,
    is_cn: bool | None = None,
) -> list:
    """线索筛选条件构造（列表与导出共用，保证两边口径一致）。"""
    from sqlalchemy import func, or_

    conds = []
    if country:
        conds.append(Lead.country == country.upper())
    if industry:
        conds.append(Lead.industry == industry)
    if min_score is not None:
        conds.append(Lead.score >= min_score)
    if grade:
        conds.append(Lead.grade == grade.upper())
    if follow_status:
        if follow_status == "unassigned":
            # 「未分配」= 显式 unassigned + 从未跟进（NULL）的共享池线索
            conds.append((Lead.follow_status.is_(None)) | (Lead.follow_status == "unassigned"))
        else:
            conds.append(Lead.follow_status == follow_status)
    if owner_id is not None:
        conds.append(Lead.owner_id == owner_id)
    if due_follow:
        # 该回访了：约定了下次跟进时间且已到期
        conds.append(Lead.next_follow_at.is_not(None) & (Lead.next_follow_at <= func.now()))
    if is_cn is not None:
        conds.append(Lead.is_cn.is_(is_cn))
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
        # 转义 %/_ 通配符：用户输入 "%" 不应变成全匹配
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        conds.append(
            or_(
                Lead.name.ilike(like, escape="\\"),
                Lead.email.ilike(like, escape="\\"),
                Lead.domain.ilike(like, escape="\\"),
                Lead.phone_e164.ilike(like, escape="\\"),
                Lead.city.ilike(like, escape="\\"),
            )
        )
    return conds


async def search_leads(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    country: str | None = None,
    industry: str | None = None,
    source: str | None = None,
    min_score: int | None = None,
    grade: str | None = None,
    whatsapp_hit: bool | None = None,
    has_website: bool | None = None,
    keyword: str | None = None,
    follow_status: str | None = None,
    owner_id: int | None = None,
    due_follow: bool | None = None,
    is_cn: bool | None = None,
    # 数据权限（§43）：scope_owner_ids 非 None 时强制限定可见 owner 集合；
    # scope_include_unassigned=共享池是否可见（个人/团队级默认可见以便认领）
    scope_owner_ids: list[int] | None = None,
    scope_include_unassigned: bool = True,
) -> tuple[list[Lead], int]:
    """线索列表筛选：国家/行业/来源/评分下限/等级/WhatsApp 检测/关键词/跟进维度。

    数据权限由调用方（endpoint）从当前用户算出 scope 参数注入——crud 层只认参数，
    保证列表/导出/统计无法绕过。
    """
    from sqlalchemy import func, or_

    conds = _lead_conditions(
        country=country,
        industry=industry,
        source=source,
        min_score=min_score,
        grade=grade,
        whatsapp_hit=whatsapp_hit,
        has_website=has_website,
        keyword=keyword,
        follow_status=follow_status,
        owner_id=owner_id,
        due_follow=due_follow,
        is_cn=is_cn,
    )
    if scope_owner_ids is not None:
        visible = Lead.owner_id.in_(scope_owner_ids)
        if scope_include_unassigned:
            visible = or_(Lead.owner_id.is_(None), visible)
        conds.append(visible)
    stmt = select(Lead)
    count_stmt = select(func.count()).select_from(Lead)
    for cond in conds:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    total = (await db.execute(count_stmt)).scalar_one()
    stmt = stmt.order_by(Lead.score.desc(), Lead.id.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list((await db.execute(stmt)).scalars().all())
    return items, total
