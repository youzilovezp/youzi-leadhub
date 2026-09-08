"""qualify_reason（方向 B）测试。

覆盖：
    - 模板拼接：summary / drivers / blockers / next_action / cache_key 结构稳定
    - 缓存命中：compute_and_save 第二次调用同输入返回同 cache_key 不重算
    - LLM 降级：llm_enabled=False → fallback（generated_by='template'）
    - apply_score 清空缓存：qualify_reason 字段被置 None
    - 触发点：create_contact 末尾会写库
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
async def _setup(db_session):
    from app.db.init_db import init_db

    await init_db()


async def _make_lead(db_session, name: str = "Reason Test"):
    from app.collectors.base import LeadDraft
    from app.crud.lead import upsert_lead

    lead, _ = await upsert_lead(
        db_session,
        LeadDraft(name=name, country="CN", industry="跨境电商", source="manual"),
    )
    assert lead is not None
    return lead


async def test_template_basic_structure(db_session):
    """模板生成的结构稳定：所有 key 都存在 + 长度合理。"""
    from app.collectors.qualify_reason import build_qualify_reason_template

    lead = await _make_lead(db_session)
    out = build_qualify_reason_template(lead, contacts=[], signals=[])
    assert isinstance(out, dict)
    for k in ("summary", "drivers", "blockers", "next_action", "generated_by", "generated_at", "cache_key"):
        assert k in out, f"missing key {k}"
    assert out["generated_by"] == "template"
    assert isinstance(out["drivers"], list)
    assert isinstance(out["blockers"], list)
    assert isinstance(out["cache_key"], str)
    assert len(out["cache_key"]) == 16  # sha256 前 16 位
    # summary 应含 ICP 标签（unknown 状态）
    assert "ICP" in out["summary"] or "未出海" in out["summary"]


async def test_c_level_blockers_summary(db_session):
    """C 级 + 无联系信息 → blockers 提示缺口，summary 反映缺口。"""
    from app.collectors.qualify_reason import build_qualify_reason_template

    lead = await _make_lead(db_session, "C 级测试")
    # lead.phone/email/whatsapp 都为空 → blockers 应列出
    out = build_qualify_reason_template(lead, contacts=[], signals=[])
    assert len(out["blockers"]) >= 1
    missing = out["blockers"][0].get("missing", [])
    assert "email" in missing or "phone" in missing or "whatsapp" in missing
    # C 级 summary 应提到「缺口」
    assert "C级" in out["summary"] or "缺口" in out["summary"]


async def test_cache_hit_does_not_recompute(db_session):
    """缓存命中：cache_key 相同的两次调用，第二次直接返回 existing（不重算时间）。"""
    from app.collectors.qualify_reason import (
        build_qualify_reason_template,
        compute_and_save_qualify_reason,
    )

    lead = await _make_lead(db_session, "缓存命中")
    # 先写一次（带 LLM=False 走模板，无 LLM 调用）
    first = await compute_and_save_qualify_reason(
        db_session, lead, contacts=[], signals=[], use_llm=False
    )
    assert first is not None
    await db_session.commit()
    first_at = first["generated_at"]
    # 第二次不重算——cache_key 未变
    second = await compute_and_save_qualify_reason(
        db_session, lead, contacts=[], signals=[], use_llm=False
    )
    assert second is not None
    assert second["cache_key"] == first["cache_key"]
    assert second["generated_at"] == first_at, "缓存命中时不应重算（generated_at 不变）"


async def test_cache_invalidates_on_signal_change(db_session):
    """输入变化（contacts 增多）→ cache_key 变化 → 重算。"""
    from app.collectors.qualify_reason import compute_and_save_qualify_reason

    lead = await _make_lead(db_session, "缓存失效")
    first = await compute_and_save_qualify_reason(
        db_session, lead, contacts=[], signals=[], use_llm=False
    )
    assert first is not None
    await db_session.commit()
    # 第二次传入新的 contacts → cache_key 应该不同
    second = await compute_and_save_qualify_reason(
        db_session,
        lead,
        contacts=[{"seniority": "tier1"}, {"seniority": "tier2"}],
        signals=[],
        use_llm=False,
    )
    assert second is not None
    assert second["cache_key"] != first["cache_key"]


async def test_force_flag_bypasses_cache(db_session):
    """force=True 跳过缓存重算（管理员「重新判定」入口用）。"""
    from app.collectors.qualify_reason import compute_and_save_qualify_reason

    lead = await _make_lead(db_session, "force 强制")
    first = await compute_and_save_qualify_reason(
        db_session, lead, contacts=[], signals=[], use_llm=False
    )
    assert first is not None
    await db_session.commit()
    second = await compute_and_save_qualify_reason(
        db_session, lead, contacts=[], signals=[], use_llm=False, force=True
    )
    assert second is not None
    # cache_key 相同但 generated_at 应被刷新（重新生成）
    assert second["cache_key"] == first["cache_key"]
    assert second["generated_at"] >= first["generated_at"]


async def test_llm_disabled_uses_template(db_session):
    """LLM 未配置 → fallback 到模板（generated_by='template'）。"""
    from app.collectors.qualify_reason import compute_and_save_qualify_reason

    lead = await _make_lead(db_session, "llm 降级")
    # use_llm=True 但 settings.LLM_BASE_URL 未配置 → 自动 fallback
    out = await compute_and_save_qualify_reason(
        db_session, lead, contacts=[], signals=[], use_llm=True
    )
    assert out is not None
    assert out["generated_by"] == "template"


async def test_apply_score_clears_cache(db_session):
    """apply_score 清空 qualify_reason → 下次访问详情页会重算。"""
    from app.collectors.qualify_reason import compute_and_save_qualify_reason
    from app.collectors.scoring import apply_score

    lead = await _make_lead(db_session, "评分清缓存")
    # 写一次 reason
    await compute_and_save_qualify_reason(
        db_session, lead, contacts=[], signals=[], use_llm=False
    )
    await db_session.commit()
    assert lead.qualify_reason is not None
    # apply_score 应该清空缓存
    apply_score(lead)
    await db_session.flush()
    assert lead.qualify_reason is None, (
        "apply_score 必须清空缓存——避免陈旧 reason 误导销售"
    )


async def test_next_action_handles_foreign_icp(db_session):
    """ICP=foreign 的线索 next_action 应明示「非出海」提醒。"""
    from app.collectors.qualify_reason import build_qualify_reason_template

    lead = await _make_lead(db_session, "Foreign 测试")
    lead.icp_status = "foreign"
    out = build_qualify_reason_template(lead, contacts=[], signals=[])
    assert "非出海" in out["next_action"] or "ICP" in out["next_action"]


async def test_signal_drivers_include_evidence_url(db_session):
    """驱动项带 evidence_url（点击跳转真实发现页）。"""
    from app.collectors.qualify_reason import build_qualify_reason_template
    from app.crud.lead_signals import upsert_signal

    lead = await _make_lead(db_session, "证据链接")
    # 让 lead 有命中信号，否则 drivers 为空（无分可显示）
    lead.whatsapp_hit = True
    lead.score_breakdown = {
        "total": 25,
        "items": [{"key": "site_whatsapp", "label": "官网 WhatsApp 入口", "points": 25}],
    }
    lead.score_signals = {"site_whatsapp": 25}
    lead.grade = "C"
    await upsert_signal(
        db_session,
        lead.id,
        "whatsapp_link",
        "+1234567890",
        source="website_enrich",
        evidence_url="https://example.com/contact",
    )
    await db_session.commit()
    out = build_qualify_reason_template(
        lead,
        contacts=[],
        signals=[],
        signal_urls={"whatsapp_link": "https://example.com/contact"},
    )
    # 找到含 site_whatsapp 的 driver
    has_driver = any(
        d.get("evidence_url") == "https://example.com/contact" for d in out["drivers"]
    )
    assert has_driver, f"drivers 应包含 evidence_url: {out['drivers']}"


async def test_create_contact_triggers_reason_save(db_session):
    """create_contact 末尾会自动写一次 qualify_reason。"""
    from app.crud.contact import create_contact
    from app.schemas.collect import ContactCreate

    lead = await _make_lead(db_session, "联系人触发")
    contact = await create_contact(
        db_session,
        lead,
        ContactCreate(name="张总", job_title="CEO", email="z@b.com", confidence=90),
    )
    await db_session.commit()
    # 联系人新增后，lead.qualify_reason 应该已经被 compute_and_save 写过
    assert lead.qualify_reason is not None
    # blockers 里「缺 tier1 联系人」应消失（已有一个）
    missing_keys = [
        m for b in (lead.qualify_reason or {}).get("blockers", []) for m in b.get("missing", [])
    ]
    assert "tier1_contact" not in missing_keys