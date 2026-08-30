"""线索采集接口：线索列表/录入/删除/批量检测 WhatsApp + 跟进 + 任务管理
+ 企业画像详情 + 联系人 + 动态事件 + CSV 导出。

权限：线索与跟进（含联系人）对所有登录用户开放（销售工作台）；
任务管控（建/改/删/执行/取消）与删线索仅管理员。
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, tuple_

from app.api.deps import CurrentUser, SessionDep, SuperUser
from app.api.perms import lead_visible, require_permission, scope_filter_params
from app.collectors import list_collectors
from app.collectors.icp import ICP_STATUS_LABELS_ZH
from app.collectors.recommend import detect_need_types, recommend_products, sales_suggestion
from app.collectors.scenes import SAAS_LABELS_ZH, SCENE_LABELS_ZH
from app.collectors.scoring import effective_dim_weights
from app.core.exceptions import BusinessError, NotFoundError, PermissionDeniedError
from app.crud.contact import (
    create_contact,
    delete_contact,
    get_contact,
    list_contacts,
    update_contact,
)
from app.crud.lead import (
    _lead_conditions,
    assign_lead,
    auto_assign_leads,
    release_lead,
    search_leads,
    upsert_lead,
)
from app.crud.lead_events import describe_dimensions
from app.crud.task_crud import list_tasks as query_tasks
from app.crud.task_crud import task_crud
from app.models.collect_task import CollectTask, CollectTaskLog
from app.models.lead import Lead, LeadContact, LeadEvent, LeadFollowUp
from app.models.user import User
from app.schemas.collect import (
    EXPORT_FIELD_KEYS,
    EXPORT_FIELDS,
    FOLLOW_STATUS_OPTIONS,
    AssignPayload,
    AutoAssignPayload,
    CollectorInfo,
    ContactCreate,
    ContactOut,
    ContactUpdate,
    FollowUpCreate,
    FollowUpOut,
    LeadCheckWhatsAppRequest,
    LeadCreate,
    LeadDetailOut,
    LeadEventOut,
    LeadImportPayload,
    LeadImportResult,
    LeadOut,
    RecommendationOut,
    SignalEvidenceOut,
    TaskCreate,
    TaskLogOut,
    TaskOut,
    TaskUpdate,
)
from app.schemas.common import PageResponse, ResponseModel
from app.services import scheduler as collect_scheduler
from app.services.task_runner import task_runner

router = APIRouter()

# 权限依赖单例（B008：默认值中的函数调用需模块级变量）
RequireAssignLead = Depends(require_permission("assign:lead"))


async def _user_display_map(db: SessionDep, user_ids: set[int | None]) -> dict[int | None, str]:
    """批量取用户展示名（昵称优先）——列表页注入 owner_name / created_by_name，避免 N+1。"""
    ids = {i for i in user_ids if i}
    if not ids:
        return {}
    rows = (
        await db.execute(select(User.id, User.nickname, User.username).where(User.id.in_(ids)))
    ).all()
    return {r.id: (r.nickname or r.username) for r in rows}


# ---------- 线索 ----------


async def _fill_lead_list_fields(db: SessionDep, items: list[Lead], outs: list[LeadOut]) -> None:
    """列表行批量注入 owner_name / contacts_count / recommended_products（防 N+1）。"""
    from sqlalchemy import func

    name_map = await _user_display_map(db, {i.owner_id for i in items})
    counts: dict[int, int] = {}
    if items:
        rows = (
            await db.execute(
                select(LeadContact.lead_id, func.count())
                .where(LeadContact.lead_id.in_([i.id for i in items]))
                .group_by(LeadContact.lead_id)
            )
        ).all()
        counts = {r[0]: r[1] for r in rows}
    for i, o in zip(items, outs, strict=True):
        o.owner_name = name_map.get(i.owner_id)
        o.contacts_count = counts.get(i.id, 0)
        o.recommended_products = [
            r["name"]
            for r in recommend_products(
                whatsapp_hit=i.whatsapp_hit,
                whatsapp_url=i.whatsapp_url,
                whatsapp_job=i.whatsapp_job,
                scenes=i.scenes,
                saas_signals=i.saas_signals,
                industry=i.industry,
                dim_saas=describe_dimensions(i.score_signals).get("saas", 0),
            )
        ]


@router.get("/leads", response_model=ResponseModel[PageResponse[LeadOut]], summary="线索列表")
async def list_leads(
    db: SessionDep,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    country: str | None = None,
    industry: str | None = None,
    source: str | None = None,
    min_score: int | None = Query(default=None, ge=0),
    grade: str | None = Query(default=None, pattern="^[SABC]$"),
    whatsapp_hit: bool | None = None,
    has_website: bool | None = None,
    keyword: str | None = None,
    follow_status: str | None = None,
    owner_id: int | None = Query(default=None, ge=1),
    due_follow: bool | None = None,
    is_cn: bool | None = None,
    icp: str | None = Query(
        default=None,
        pattern="^(qualified|cn_domestic|foreign|unknown|all)$",
        description="ICP 资格：缺省=排除非中国企业；all=不过滤",
    ),
):
    # 数据权限（§43）：own/team 级强制限定可见 owner，接口层无旁路
    scope_ids, include_unassigned = await scope_filter_params(db, user)
    items, total = await search_leads(
        db,
        page=page,
        page_size=page_size,
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
        icp=icp,
        scope_owner_ids=scope_ids,
        scope_include_unassigned=include_unassigned,
    )
    outs = [LeadOut.model_validate(i) for i in items]
    await _fill_lead_list_fields(db, items, outs)
    return ResponseModel(
        data=PageResponse[LeadOut](
            items=outs,
            total=total,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/leads", response_model=ResponseModel[LeadOut], summary="手工录入线索")
async def create_lead(db: SessionDep, _user: CurrentUser, payload: LeadCreate):
    from app.collectors.base import LeadDraft

    draft = LeadDraft(
        source=payload.note_source or "manual",
        name=payload.name,
        country=payload.country,
        city=payload.city,
        industry=payload.industry,
        address=payload.address,
        phone_raw=payload.phone,
        website=payload.website,
        email=payload.email,
    )
    lead, _ = await upsert_lead(db, draft)
    await db.commit()
    # 合并路径 UPDATE 后 updated_at（server onupdate）处于 expired 状态，
    # 直接 model_validate 会触发懒加载 IO → MissingGreenlet 422。显式刷新。
    await db.refresh(lead)
    return ResponseModel(data=LeadOut.model_validate(lead))


@router.post(
    "/leads/import",
    response_model=ResponseModel[LeadImportResult],
    summary="Seed Pool 批量导入企业种子（CSV 文本，走去重合并）",
)
async def import_leads(db: SessionDep, user: CurrentUser, payload: LeadImportPayload):
    """批量导入中国企业种子（PRD §三 模块①）：CSV 首行表头可选。

    列：name(必填),website,phone,country,city,industry——每行走 upsert_lead
    去重合并（domain/电话/名称三身份列反查），source=seed_import。
    """
    import csv as csv_mod
    import io as io_mod

    from app.collectors.base import LeadDraft

    # 权限：录入走 lead:write 口径；未配角色的登录用户沿用列表/手工录入的宽口径
    reader = csv_mod.reader(io_mod.StringIO(payload.csv_text.strip()))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise BusinessError(code=40001, message="CSV 内容为空")

    # 首行是表头（含 name 字样）则跳过
    first = [c.strip().lower() for c in rows[0]]
    if "name" in first:
        header = {c: i for i, c in enumerate(first)}
        rows = rows[1:]
    else:
        header = {c: i for i, c in enumerate(("name", "website", "phone", "country", "city", "industry"))}
    if not rows:
        raise BusinessError(code=40001, message="CSV 没有数据行（只有表头或空行）")

    result = LeadImportResult(total=len(rows))

    def _col(row: list[str], key: str) -> str | None:
        i = header.get(key)
        if i is None or i >= len(row):
            return None
        v = row[i].strip()
        return v or None

    for idx, row in enumerate(rows, start=1):

        name = _col(row, "name")
        if not name:
            result.skipped += 1
            if len(result.errors) < 20:
                result.errors.append(f"第 {idx} 行缺少企业名称，已跳过")
            continue
        try:
            draft = LeadDraft(
                source="seed_import",
                name=name[:255],
                website=_col(row, "website"),
                phone_raw=_col(row, "phone"),
                country=(_col(row, "country") or "").upper()[:8] or None,
                city=_col(row, "city"),
                industry=_col(row, "industry"),
                is_cn=payload.is_cn,
            )
            lead, created = await upsert_lead(db, draft)
            if created:
                result.created += 1
            else:
                result.merged += 1
        except Exception as exc:  # noqa: BLE001  单行失败不中断整批
            result.skipped += 1
            if len(result.errors) < 20:
                result.errors.append(f"第 {idx} 行导入失败：{type(exc).__name__}: {str(exc)[:80]}")
    await db.commit()
    return ResponseModel(data=result)


@router.get("/leads/export", summary="导出线索 CSV（当前筛选口径，UTF-8 BOM 兼容 Excel）")
async def export_leads(
    db: SessionDep,
    user: CurrentUser,
    fields: str | None = Query(
        default=None, description="逗号分隔的字段 key（见 EXPORT_FIELDS），缺省=全部"
    ),
    limit: int = Query(default=5000, ge=1, le=50000),
    country: str | None = None,
    industry: str | None = None,
    source: str | None = None,
    min_score: int | None = Query(default=None, ge=0),
    grade: str | None = Query(default=None, pattern="^[SABC]$"),
    whatsapp_hit: bool | None = None,
    has_website: bool | None = None,
    keyword: str | None = None,
    follow_status: str | None = None,
    owner_id: int | None = Query(default=None, ge=1),
    due_follow: bool | None = None,
    is_cn: bool | None = None,
    icp: str | None = Query(
        default=None,
        pattern="^(qualified|cn_domestic|foreign|unknown|all)$",
        description="ICP 资格：缺省=排除非中国企业；all=不过滤（与列表同口径）",
    ),
):
    """注意：本路由必须声明在 GET /leads/{lead_id} 之前（否则 "export" 被当作 lead_id）。"""
    if fields:
        selected = [k.strip() for k in fields.split(",") if k.strip() in EXPORT_FIELD_KEYS]
    else:
        selected = [k for k, _ in EXPORT_FIELDS]
    if not selected:
        raise BusinessError(code=40001, message="未选择有效的导出字段")

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
        icp=icp,
    )
    # 数据权限与列表同口径（§43），导出无法绕过
    scope_ids, include_unassigned = await scope_filter_params(db, user)
    if scope_ids is not None:
        from sqlalchemy import or_

        visible = Lead.owner_id.in_(scope_ids)
        if include_unassigned:
            visible = or_(Lead.owner_id.is_(None), visible)
        conds.append(visible)
    stmt_base = select(Lead)
    for cond in conds:
        stmt_base = stmt_base.where(cond)

    async def _fetch_chunk(last_key: tuple[int, int] | None, size: int) -> list[Lead]:
        """按 (score, id) 键集分页取数——流式导出不在内存物化全量。"""
        stmt = stmt_base
        if last_key is not None:
            stmt = stmt.where(tuple_(Lead.score, Lead.id) < last_key)
        stmt = stmt.order_by(Lead.score.desc(), Lead.id.desc()).limit(size)
        return list((await db.execute(stmt)).scalars().all())

    def _cell_factory(name_map: dict, contact_lines: dict):
        def _cell(lead: Lead, key: str) -> str:
            value: Any
            if key.startswith("dim_"):
                value = describe_dimensions(lead.score_signals).get(key[4:], "")
            elif key == "contacts_count":
                value = len(contact_lines.get(lead.id, "").split("; ")) if contact_lines.get(lead.id) else 0
            elif key == "contacts_summary":
                value = contact_lines.get(lead.id, "")
            elif key == "recommended_products":
                value = "; ".join(
                    r["name"]
                    for r in recommend_products(
                        whatsapp_hit=lead.whatsapp_hit,
                        whatsapp_url=lead.whatsapp_url,
                        whatsapp_job=lead.whatsapp_job,
                        scenes=lead.scenes,
                        saas_signals=lead.saas_signals,
                        industry=lead.industry,
                        dim_saas=describe_dimensions(lead.score_signals).get("saas", 0),
                    )
                )
            elif key == "owner_name":
                value = name_map.get(lead.owner_id, "")
            elif key == "icp_status":
                value = ICP_STATUS_LABELS_ZH.get(lead.icp_status, lead.icp_status or "")
            elif key == "scenes":
                value = "; ".join(SCENE_LABELS_ZH.get(s, s) for s in (lead.scenes or []))
            elif key == "saas_signals":
                value = "; ".join(
                    f"{SAAS_LABELS_ZH.get(k, k)}×{v}" for k, v in (lead.saas_signals or {}).items()
                )
            elif key == "sources":
                value = "; ".join(r.get("source", "") for r in (lead.sources or []))
            elif key == "social":
                value = "; ".join(f"{k}:{v}" for k, v in (lead.social or {}).items())
            elif key == "job_urls":
                value = "; ".join(lead.job_urls or [])
            elif key == "whatsapp_numbers":
                value = "; ".join((lead.whatsapp_numbers or [])[:8])
            elif key == "need_types":
                value = "; ".join(
                    n["label"]
                    for n in detect_need_types(
                        whatsapp_hit=lead.whatsapp_hit,
                        whatsapp_url=lead.whatsapp_url,
                        whatsapp_numbers=lead.whatsapp_numbers,
                        whatsapp_job=lead.whatsapp_job,
                        scenes=lead.scenes,
                        saas_signals=lead.saas_signals,
                        sources=lead.sources,
                    )
                )
            elif isinstance(getattr(lead, key, None), bool):
                value = "是" if getattr(lead, key) else "否"
            else:
                value = getattr(lead, key, "")
            if value is None:
                return ""
            return str(value)
        return _cell

    selected_set = set(selected)

    async def _csv_chunks():
        """分批（1000 行/批）产出 CSV 字节流：单请求内存峰值 = 一批，不再是全量。"""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([label for k, label in EXPORT_FIELDS if k in selected_set])
        yield b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")

        _cell = None
        last_key: tuple[int, int] | None = None
        exported = 0
        while exported < limit:
            size = min(1000, limit - exported)
            chunk = await _fetch_chunk(last_key, size)
            if not chunk:
                break
            name_map = await _user_display_map(db, {i.owner_id for i in chunk})
            contact_lines: dict[int, str] = {}
            rows = (
                await db.execute(
                    select(LeadContact).where(LeadContact.lead_id.in_([i.id for i in chunk]))
                )
            )
            grouped: dict[int, list[str]] = {}
            for c in rows.scalars():
                label = c.name or c.email or "未命名"
                if c.job_title:
                    label = f"{label}({c.job_title})"
                if c.email and c.email != label:
                    label = f"{label}/{c.email}"
                grouped.setdefault(c.lead_id, []).append(label)
            contact_lines = {k: "; ".join(v) for k, v in grouped.items()}
            _cell = _cell_factory(name_map, contact_lines)

            buf = io.StringIO()
            writer = csv.writer(buf)
            for lead in chunk:
                writer.writerow([_cell(lead, k) for k in selected])
            yield buf.getvalue().encode("utf-8")
            exported += len(chunk)
            last_key = (chunk[-1].score, chunk[-1].id)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        _csv_chunks(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="leads_{ts}.csv"'},
    )


@router.get(
    "/leads/{lead_id}",
    response_model=ResponseModel[LeadDetailOut],
    summary="线索详情（企业画像：六维分/联系人/事件/推荐/销售建议）",
)
async def get_lead_detail(db: SessionDep, user: CurrentUser, lead_id: int):
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise NotFoundError("线索不存在")
    # 数据权限（§43）：受限范围外 + 非共享池 → 视为不存在
    scope_ids, _include = await scope_filter_params(db, user)
    if not lead_visible(lead.owner_id, scope_ids):
        raise NotFoundError("线索不存在")

    contacts = await list_contacts(db, lead_id)
    events = list(
        (
            await db.execute(
                select(LeadEvent)
                .where(LeadEvent.lead_id == lead_id)
                .order_by(LeadEvent.id.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    follow_ups = list(
        (
            await db.execute(
                select(LeadFollowUp)
                .where(LeadFollowUp.lead_id == lead_id)
                .order_by(LeadFollowUp.id.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )

    dims = describe_dimensions(lead.score_signals)
    recs = recommend_products(
        whatsapp_hit=lead.whatsapp_hit,
        whatsapp_url=lead.whatsapp_url,
        whatsapp_job=lead.whatsapp_job,
        scenes=lead.scenes,
        saas_signals=lead.saas_signals,
        industry=lead.industry,
        dim_saas=dims.get("saas", 0),
    )
    suggestion = sales_suggestion(
        grade=lead.grade,
        whatsapp_url=lead.whatsapp_url,
        whatsapp_job=lead.whatsapp_job,
        saas_signals=lead.saas_signals,
        has_tier1_contact=any(c.seniority == "tier1" for c in contacts),
        products=recs,
    )

    out = LeadDetailOut.model_validate(lead)
    out.owner_name = (await _user_display_map(db, {lead.owner_id})).get(lead.owner_id)
    out.contacts_count = len(contacts)
    out.recommended_products = [r["name"] for r in recs]
    out.dimensions = dims
    out.dimension_weights = effective_dim_weights()
    out.contacts = [ContactOut.model_validate(c) for c in contacts]
    out.events = [LeadEventOut.model_validate(e) for e in events]
    fu_name_map = await _user_display_map(db, {f.user_id for f in follow_ups})
    out.follow_ups = []
    for f in follow_ups:
        fo = FollowUpOut.model_validate(f)
        fo.user_name = fu_name_map.get(f.user_id)
        out.follow_ups.append(fo)
    out.recommendations = [RecommendationOut(**r) for r in recs]
    out.sales_suggestion = suggestion
    out.need_types = detect_need_types(
        whatsapp_hit=lead.whatsapp_hit,
        whatsapp_url=lead.whatsapp_url,
        whatsapp_numbers=lead.whatsapp_numbers,
        whatsapp_job=lead.whatsapp_job,
        scenes=lead.scenes,
        saas_signals=lead.saas_signals,
        sources=lead.sources,
    )
    # 信号体系（§4.2/§4.3/§4.1）：出海/招聘/广告信号 + 证据链
    from app.crud.lead_signals import SIGNAL_TYPE_LABELS_ZH, list_signals

    out.overseas_signals = dict(lead.overseas_signals or {})
    out.score_breakdown = dict(lead.score_breakdown or {})
    out.wa_business = bool(lead.wa_business)
    out.job_signals = dict(lead.job_signals or {})
    out.ad_count = lead.ad_count or 0
    out.last_ad_at = lead.last_ad_at
    signal_rows = await list_signals(db, lead_id)
    out.signals = [
        SignalEvidenceOut(
            id=r.id,
            signal_type=r.signal_type,
            signal_type_label=SIGNAL_TYPE_LABELS_ZH.get(r.signal_type, r.signal_type),
            value=r.value,
            evidence_url=r.evidence_url,
            evidence_raw=r.evidence_raw,
            confidence=r.confidence,
            source=r.source,
            first_seen=r.first_seen,
            last_seen=r.last_seen,
        )
        for r in signal_rows
    ]
    return ResponseModel(data=out)


@router.get(
    "/leads/{lead_id}/events",
    response_model=ResponseModel[PageResponse[LeadEventOut]],
    summary="线索动态事件（时间线）",
)
async def list_lead_events(
    db: SessionDep,
    user: CurrentUser,
    lead_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    from sqlalchemy import func

    await _get_visible_lead(db, lead_id, user)
    base = select(LeadEvent).where(LeadEvent.lead_id == lead_id)
    total = (
        await db.execute(select(func.count()).select_from(LeadEvent).where(LeadEvent.lead_id == lead_id))
    ).scalar_one()
    items = list(
        (
            await db.execute(
                base.order_by(LeadEvent.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return ResponseModel(
        data=PageResponse[LeadEventOut](
            items=[LeadEventOut.model_validate(x) for x in items],
            total=total,
            page=page,
            page_size=page_size,
        )
    )


# ---------- 联系人（销售工作台） ----------


async def _get_visible_lead(db: SessionDep, lead_id: int, user: User) -> Lead:
    """取线索并做数据权限校验（与列表/详情口径一致：受限范围外视为不存在）。"""
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise NotFoundError("线索不存在")
    scope_ids, _include = await scope_filter_params(db, user)
    if not lead_visible(lead.owner_id, scope_ids):
        raise NotFoundError("线索不存在")
    return lead


@router.get(
    "/leads/{lead_id}/contacts",
    response_model=ResponseModel[list[ContactOut]],
    summary="联系人列表",
)
async def list_lead_contacts(db: SessionDep, user: CurrentUser, lead_id: int):
    await _get_visible_lead(db, lead_id, user)
    return ResponseModel(data=[ContactOut.model_validate(c) for c in await list_contacts(db, lead_id)])


@router.post(
    "/leads/{lead_id}/contacts",
    response_model=ResponseModel[ContactOut],
    summary="新增联系人（seniority 按职位自动分层）",
)
async def create_lead_contact(
    db: SessionDep, user: CurrentUser, lead_id: int, payload: ContactCreate
):
    lead = await _get_visible_lead(db, lead_id, user)
    contact = await create_contact(db, lead, payload, created_by=user.id)
    await db.commit()
    await db.refresh(contact)
    return ResponseModel(data=ContactOut.model_validate(contact))


@router.put(
    "/leads/{lead_id}/contacts/{contact_id}",
    response_model=ResponseModel[ContactOut],
    summary="编辑联系人",
)
async def update_lead_contact(
    db: SessionDep, user: CurrentUser, lead_id: int, contact_id: int, payload: ContactUpdate
):
    lead = await _get_visible_lead(db, lead_id, user)
    contact = await get_contact(db, lead_id, contact_id)
    if contact is None:
        raise NotFoundError("联系人不存在")
    contact = await update_contact(db, lead, contact, payload, created_by=user.id)
    await db.commit()
    await db.refresh(contact)
    return ResponseModel(data=ContactOut.model_validate(contact))


@router.delete(
    "/leads/{lead_id}/contacts/{contact_id}",
    response_model=ResponseModel[None],
    summary="删除联系人",
)
async def delete_lead_contact(
    db: SessionDep, user: CurrentUser, lead_id: int, contact_id: int
):
    lead = await _get_visible_lead(db, lead_id, user)
    contact = await get_contact(db, lead_id, contact_id)
    if contact is None:
        raise NotFoundError("联系人不存在")
    await delete_contact(db, lead, contact)
    await db.commit()
    return ResponseModel()


# ---------- 分配（PRD §24/§44：主管分配/转移/释放 + 自动分配 + 撞单锁定） ----------


@router.post(
    "/leads/{lead_id}/assign",
    response_model=ResponseModel[LeadOut],
    summary="分配/转移跟进人（撞单锁定：分配后其他销售只读）",
)
async def assign_lead_endpoint(
    db: SessionDep,
    lead_id: int,
    payload: AssignPayload,
    user: User = RequireAssignLead,
):
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise NotFoundError("线索不存在")
    try:
        await assign_lead(db, lead, payload.owner_id, assigned_by=user.id)
    except ValueError as exc:
        raise BusinessError(code=40001, message=str(exc)) from exc
    await db.commit()
    await db.refresh(lead)
    out = LeadOut.model_validate(lead)
    out.owner_name = (await _user_display_map(db, {lead.owner_id})).get(lead.owner_id)
    return ResponseModel(data=out)


@router.post(
    "/leads/{lead_id}/release",
    response_model=ResponseModel[LeadOut],
    summary="释放回共享池（主管可释放/重新分配）",
)
async def release_lead_endpoint(
    db: SessionDep,
    lead_id: int,
    user: User = RequireAssignLead,
):
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise NotFoundError("线索不存在")
    await release_lead(db, lead, released_by=user.id)
    await db.commit()
    await db.refresh(lead)
    return ResponseModel(data=LeadOut.model_validate(lead))


@router.post(
    "/leads/auto-assign",
    response_model=ResponseModel[dict],
    summary="自动分配：共享池线索按当前负载轮转分给候选销售（§24）",
)
async def auto_assign_endpoint(
    db: SessionDep,
    payload: AutoAssignPayload,
    _user: User = RequireAssignLead,
):
    assigned, counts = await auto_assign_leads(
        db,
        candidate_owner_ids=payload.owner_ids,
        max_per_owner=payload.max_per_owner,
        grade=payload.grade,
        min_score=payload.min_score,
        industry=payload.industry,
        country=payload.country,
        limit=payload.limit,
    )
    await db.commit()
    name_map = await _user_display_map(db, set(payload.owner_ids))
    return ResponseModel(
        data={
            "assigned_count": len(assigned),
            "per_owner": [
                {"owner_id": uid, "owner_name": name_map.get(uid), "count": cnt}
                for uid, cnt in counts.items()
            ],
        }
    )


@router.delete("/leads/{lead_id}", response_model=ResponseModel[None], summary="删除线索")
async def delete_lead(db: SessionDep, _user: SuperUser, lead_id: int):
    from sqlalchemy import delete as sa_delete

    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise NotFoundError("线索不存在")
    # SQLite 默认不开外键级联，子表显式删（PG 下双保险；同 delete_task 的日志处理）
    await db.execute(sa_delete(LeadContact).where(LeadContact.lead_id == lead_id))
    await db.execute(sa_delete(LeadEvent).where(LeadEvent.lead_id == lead_id))
    await db.execute(sa_delete(LeadFollowUp).where(LeadFollowUp.lead_id == lead_id))
    await db.delete(lead)
    await db.commit()
    return ResponseModel()


@router.post(
    "/leads/check-whatsapp",
    response_model=ResponseModel[TaskOut],
    summary="勾选线索 → 创建隐式 website_enrich 任务（复用进度/取消/闸门）",
)
async def check_whatsapp(db: SessionDep, user: CurrentUser, payload: LeadCheckWhatsAppRequest):
    task = await task_crud.create(
        db,
        TaskCreate(
            collector="website_enrich",
            name=f"手动检测 WhatsApp - 选中 {len(payload.lead_ids)} 条",
            params={"lead_ids": payload.lead_ids},
        ).model_dump()
        | {"is_implicit": True, "created_by": user.id},
    )
    await db.commit()
    await task_runner.enqueue(task.id)
    await db.refresh(task)  # enqueue 在独立会话改了 status，刷新再返回
    return ResponseModel(data=TaskOut.model_validate(task))


# ---------- 跟进（销售工作台） ----------


@router.get(
    "/follow-options",
    response_model=ResponseModel[dict],
    summary="跟进弹窗选项（状态词表 + 可选跟进人）",
)
async def follow_options(db: SessionDep, _user: CurrentUser):
    rows = (
        await db.execute(
            select(User.id, User.nickname, User.username)
            .where(User.is_active)
            .order_by(User.id)
        )
    ).all()
    return ResponseModel(
        data={
            "statuses": FOLLOW_STATUS_OPTIONS,
            "users": [{"value": r.id, "label": r.nickname or r.username} for r in rows],
        }
    )


@router.post(
    "/leads/{lead_id}/follow-up",
    response_model=ResponseModel[LeadOut],
    summary="记录跟进：更新线索跟进人/状态/时间并写一条历史",
)
async def create_follow_up(
    db: SessionDep, user: CurrentUser, lead_id: int, payload: FollowUpCreate
):
    lead = await _get_visible_lead(db, lead_id, user)

    # 指派了跟进人时校验存在（FK 兜底，避免 commit 才炸 IntegrityError）
    owner_id = payload.owner_id or user.id
    if owner_id != user.id and await db.get(User, owner_id) is None:
        raise BusinessError(code=40001, message=f"跟进人不存在：{owner_id}")

    # 撞单锁定语义做实：把线索改派给他人需要 assign:lead 权限（主管），
    # 普通销售只能跟进共享池/自己的线索（owner 落自己）——不能再越权抢改 owner
    from app.api.perms import user_permission_codes

    has_assign = "assign:lead" in user_permission_codes(user)
    if owner_id != user.id and not has_assign:
        raise PermissionDeniedError("只有主管可以指派跟进人（assign:lead）")
    if lead.owner_id not in (None, user.id) and not has_assign:
        raise PermissionDeniedError("该线索已由其他销售跟进，不能直接跟进")

    db.add(
        LeadFollowUp(
            lead_id=lead.id,
            user_id=user.id,
            status=payload.status,
            note=payload.note,
            next_follow_at=payload.next_follow_at,
        )
    )
    lead.owner_id = owner_id
    lead.follow_status = payload.status
    lead.last_followed_at = datetime.now(timezone.utc)
    lead.next_follow_at = payload.next_follow_at
    await db.commit()
    await db.refresh(lead)

    out = LeadOut.model_validate(lead)
    out.owner_name = (await _user_display_map(db, {owner_id})).get(owner_id)
    return ResponseModel(data=out)


@router.get(
    "/leads/{lead_id}/follow-ups",
    response_model=ResponseModel[list[FollowUpOut]],
    summary="跟进历史（弹窗时间线，最近 50 条）",
)
async def list_follow_ups(db: SessionDep, user: CurrentUser, lead_id: int):
    if await _get_visible_lead(db, lead_id, user) is None:
        raise NotFoundError("线索不存在")
    stmt = (
        select(LeadFollowUp)
        .where(LeadFollowUp.lead_id == lead_id)
        .order_by(LeadFollowUp.id.desc())
        .limit(50)
    )
    items = list((await db.execute(stmt)).scalars().all())
    name_map = await _user_display_map(db, {x.user_id for x in items})
    outs = []
    for x in items:
        o = FollowUpOut.model_validate(x)
        o.user_name = name_map.get(x.user_id)
        outs.append(o)
    return ResponseModel(data=outs)


# ---------- 任务 ----------


@router.get("/collectors", response_model=ResponseModel[list[CollectorInfo]], summary="采集器列表")
async def get_collectors(_user: CurrentUser):
    return ResponseModel(data=[CollectorInfo(**c) for c in list_collectors()])


@router.get("/geo-options", response_model=ResponseModel[dict], summary="国家/城市选项（表单联动数据源）")
async def get_geo_options(_user: CurrentUser):
    from app.collectors.base import CITY_OPTIONS_BY_COUNTRY, COUNTRY_OPTIONS

    return ResponseModel(data={"countries": COUNTRY_OPTIONS, "cities_by_country": CITY_OPTIONS_BY_COUNTRY})


@router.get("/industries", response_model=ResponseModel[list[dict]], summary="线索行业选项（库存 distinct，筛选数据源）")
async def lead_industries(db: SessionDep, _user: CurrentUser):
    """行业筛选下拉的数据源：直接取库里实际存在的 industry 值。

    不用预设词表——google_maps 存关键词、OSM 存标签值、手工录入任意填，
    distinct 才能保证「选项里有的就能查到」。label 是中文展示名
    （词表映射，未收录原样显示），value 保持原 token 保证筛选精确。
    """
    from sqlalchemy import func

    from app.collectors.industry_labels import INDUSTRY_LABELS_ZH

    rows = (
        await db.execute(
            select(Lead.industry, func.count())
            .where(Lead.industry.is_not(None), Lead.industry != "")
            .group_by(Lead.industry)
            .order_by(func.count().desc())
        )
    ).all()
    return ResponseModel(
        data=[
            {"label": INDUSTRY_LABELS_ZH.get(name, name), "value": name, "count": cnt}
            for name, cnt in rows
        ]
    )


@router.get("/tasks", response_model=ResponseModel[PageResponse[TaskOut]], summary="任务列表")
async def list_tasks(
    db: SessionDep,
    _user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    collector: str | None = None,
    status: str | None = None,
):
    items, total = await query_tasks(
        db, page=page, page_size=page_size, collector=collector, status=status
    )
    name_map = await _user_display_map(db, {t.created_by for t in items})
    outs = []
    for t in items:
        o = TaskOut.model_validate(t)
        o.created_by_name = name_map.get(t.created_by)
        outs.append(o)
    return ResponseModel(
        data=PageResponse[TaskOut](
            items=outs,
            total=total,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/tasks", response_model=ResponseModel[TaskOut], summary="创建任务")
async def create_task(db: SessionDep, user: SuperUser, payload: TaskCreate):
    from app.collectors import get_collector

    collector = get_collector(payload.collector)
    if collector is None:
        raise BusinessError(code=40001, message=f"未知采集器：{payload.collector}")
    collector.validate_params(payload.params)
    task = await task_crud.create(
        db,
        {
            "name": payload.name or collector.title,
            "collector": payload.collector,
            "params": payload.params,
            "cron_expr": payload.cron_expr,
            "is_implicit": False,
            "created_by": user.id,
        },
    )
    await db.commit()
    if payload.cron_expr:
        await collect_scheduler.sync()
    else:
        await task_runner.enqueue(task.id)
        await db.refresh(task)  # enqueue 在独立会话改了 status，刷新再返回
    return ResponseModel(data=TaskOut.model_validate(task))


@router.get("/tasks/{task_id}", response_model=ResponseModel[TaskOut], summary="任务详情")
async def get_task(db: SessionDep, _user: CurrentUser, task_id: int):
    task = await task_crud.get(db, task_id)
    if task is None:
        raise NotFoundError("任务不存在")
    return ResponseModel(data=TaskOut.model_validate(task))


@router.put("/tasks/{task_id}", response_model=ResponseModel[TaskOut], summary="更新任务")
async def update_task(db: SessionDep, _user: SuperUser, task_id: int, payload: TaskUpdate):
    task = await task_crud.get(db, task_id)
    if task is None:
        raise NotFoundError("任务不存在")
    data = payload.model_dump(exclude_unset=True)
    if "params" in data and data["params"] is not None:
        from app.collectors import get_collector

        collector = get_collector(task.collector)
        if collector is not None:
            collector.validate_params({**(task.params or {}), **data["params"]})
    await task_crud.update(db, task, data)
    if "cron_expr" in data or "enabled" in data:
        await collect_scheduler.sync()
    return ResponseModel(data=TaskOut.model_validate(task))


@router.delete("/tasks/{task_id}", response_model=ResponseModel[None], summary="删除任务")
async def delete_task(db: SessionDep, _user: SuperUser, task_id: int):
    from sqlalchemy import delete as sa_delete

    task = await task_crud.get(db, task_id)
    if task is None:
        raise NotFoundError("任务不存在")
    if task.status == "running":
        raise BusinessError(code=40001, message="任务运行中，请先取消")
    # SQLite 默认不开外键级联，日志显式删（PG 下双保险）
    await db.execute(sa_delete(CollectTaskLog).where(CollectTaskLog.task_id == task_id))
    await db.delete(task)
    await db.commit()
    await collect_scheduler.sync()
    return ResponseModel()


@router.post("/tasks/{task_id}/run", response_model=ResponseModel[None], summary="执行任务")
async def run_task(db: SessionDep, _user: SuperUser, task_id: int):
    task = await task_crud.get(db, task_id)
    if task is None:
        raise NotFoundError("任务不存在")
    await task_runner.enqueue(task_id)
    return ResponseModel()


@router.post("/tasks/{task_id}/cancel", response_model=ResponseModel[None], summary="取消任务")
async def cancel_task(db: SessionDep, _user: SuperUser, task_id: int):
    task = await task_crud.get(db, task_id)
    if task is None:
        raise NotFoundError("任务不存在")
    ok = await task_runner.cancel(task_id)
    if not ok:
        raise BusinessError(code=40001, message=f"任务状态 {task.status}，无需取消")
    return ResponseModel()


@router.get(
    "/tasks/{task_id}/logs",
    response_model=ResponseModel[PageResponse[TaskLogOut]],
    summary="任务日志（after_id 增量轮询）",
)
async def get_task_logs(
    db: SessionDep,
    _user: CurrentUser,
    task_id: int,
    after_id: int = Query(default=0, ge=0),
    page_size: int = Query(default=100, ge=1, le=500),
):
    stmt = (
        select(CollectTaskLog)
        .where(CollectTaskLog.task_id == task_id, CollectTaskLog.id > after_id)
        .order_by(CollectTaskLog.id)
        .limit(page_size)
    )
    items = list((await db.execute(stmt)).scalars().all())
    return ResponseModel(
        data=PageResponse[TaskLogOut](
            items=[TaskLogOut.model_validate(x) for x in items],
            total=len(items),
            page=1,
            page_size=page_size,
        )
    )


# ---------- 汇总（任务列表页快捷统计，避免前端拼多个请求）----------
@router.get("/stats", response_model=ResponseModel[dict], summary="采集总览统计")
async def collect_stats(db: SessionDep, _user: CurrentUser):
    from sqlalchemy import func

    total = (await db.execute(select(func.count()).select_from(Lead))).scalar_one()
    whatsapp = (
        await db.execute(select(func.count()).select_from(Lead).where(Lead.whatsapp_hit))
    ).scalar_one()
    high_intent = (
        await db.execute(select(func.count()).select_from(Lead).where(Lead.score >= 40))
    ).scalar_one()
    # 等级分布：S/A/B/C（销售优先级口径）
    grade_rows = (
        await db.execute(select(Lead.grade, func.count()).group_by(Lead.grade))
    ).all()
    grade_counts = {g: 0 for g in ("S", "A", "B", "C")}
    for g, cnt in grade_rows:
        grade_counts[g] = cnt
    running = (
        await db.execute(
            select(func.count())
            .select_from(CollectTask)
            .where(CollectTask.status.in_(["queued", "running"]))
        )
    ).scalar_one()
    # 跟进维度：从未跟进的（共享池待认领）+ 约定回访时间已到期的
    pending_follow = (
        await db.execute(
            select(func.count()).select_from(Lead).where(Lead.follow_status.is_(None))
        )
    ).scalar_one()
    due_follow = (
        await db.execute(
            select(func.count()).select_from(Lead).where(Lead.next_follow_at <= func.now())
        )
    ).scalar_one()
    # 商机维度：中国出海企业数 + FB 主页带 WA 私域按钮数
    cn_leads = (
        await db.execute(select(func.count()).select_from(Lead).where(Lead.is_cn))
    ).scalar_one()
    fb_wa_leads = (
        await db.execute(select(func.count()).select_from(Lead).where(Lead.fb_whatsapp))
    ).scalar_one()
    # ICP 二重门分布：qualified（销售池）/ cn_domestic（培育）/ foreign / unknown
    icp_rows = (
        await db.execute(select(Lead.icp_status, func.count()).group_by(Lead.icp_status))
    ).all()
    icp_counts = {s: 0 for s in ("qualified", "cn_domestic", "foreign", "unknown")}
    for s, cnt in icp_rows:
        icp_counts[s] = cnt
    # 月度口径：本月新增线索 + 本月成交（follow_status=won 的数据飞轮回传，
    # §二「成交/未成交」——CRM 商机金额已按需求边界移除，成交按线索状态统计）
    # 月初用 Python 算：date_trunc 是 PG 专属，SQLite 测试库没有
    from datetime import datetime, timezone

    month_start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    month_new = (
        await db.execute(
            select(func.count()).select_from(Lead).where(Lead.created_at >= month_start)
        )
    ).scalar_one()
    month_won = (
        await db.execute(
            select(func.count()).select_from(Lead).where(
                Lead.follow_status == "won", Lead.last_followed_at.is_not(None),
                Lead.last_followed_at >= month_start,
            )
        )
    ).scalar_one()
    return ResponseModel(
        data={
            "total_leads": total,
            "whatsapp_leads": whatsapp,
            "high_intent_leads": high_intent,
            "grade_counts": grade_counts,
            "active_tasks": running,
            "pending_leads": pending_follow,
            "due_follow_leads": due_follow,
            "cn_leads": cn_leads,
            "fb_wa_leads": fb_wa_leads,
            "icp_counts": icp_counts,
            "month_new_leads": month_new,
            "month_won_count": month_won,
        }
    )
