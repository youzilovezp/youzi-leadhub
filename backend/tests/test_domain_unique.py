"""domain 唯一索引与写入守卫测试（2026-08-31 审计批次5）。"""

import pytest
from sqlalchemy.exc import IntegrityError


@pytest.mark.asyncio
async def test_db_rejects_duplicate_domain(client):
    """DB 层（部分唯一索引，create_all 与迁移 f7c2a91d4e21 同构）拒绝双写同域。"""
    from app.db.session import async_session
    from app.models.lead import Lead

    async with async_session() as s:
        s.add(Lead(name="域唯一公司A", domain="dup-domain-test.com", dedupe_key="namecity:dup-a"))
        await s.commit()

    async with async_session() as s:
        s.add(Lead(name="域唯一公司B", domain="dup-domain-test.com", dedupe_key="namecity:dup-b"))
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()

    # NULL 不受约束（无官网线索可以有多条）
    async with async_session() as s:
        s.add(Lead(name="无域公司C", domain=None, dedupe_key="namecity:dup-c"))
        s.add(Lead(name="无域公司D", domain=None, dedupe_key="namecity:dup-d"))
        await s.commit()


@pytest.mark.asyncio
async def test_merge_does_not_grab_taken_domain(client):
    """OR 反查命中两行时，合并目标不得抢写他人持有的 domain（撞索引）。"""
    from sqlalchemy import select

    from app.collectors.base import LeadDraft
    from app.crud.lead import _namecity_key, upsert_lead  # noqa: SLF001
    from app.db.session import async_session
    from app.models.lead import Lead

    nck = _namecity_key("抢域测试公司", None)
    async with async_session() as s:
        # 低 id：namecity 身份、无官网
        a = Lead(name="抢域测试公司", dedupe_key=nck, namecity_key=nck)
        s.add(a)
        await s.flush()
        a_id = a.id
        # 高 id：持有 domain
        b = Lead(name="抢域测试公司官网", domain="grab-test.com", website="https://grab-test.com",
                 dedupe_key="domain:grab-test.com")
        s.add(b)
        await s.commit()

    # draft = 同名（命中 A 的 namecity）+ 带官网（命中 B 的 domain）→ 合并进低 id 的 A，
    # 但 domain 已被 B 持有 → 不写 domain，不撞唯一索引
    draft = LeadDraft(source="web_search", name="抢域测试公司", website="https://grab-test.com")
    async with async_session() as s:
        lead, created = await upsert_lead(s, draft)
        await s.commit()
        await s.refresh(lead)
        assert created is False
        assert lead.id == a_id
        assert lead.domain is None  # 域归 B，A 不抢
        await s.rollback()

    async with async_session() as s:
        b_row = (await s.execute(select(Lead).where(Lead.id == b.id))).scalar_one()
        assert b_row.domain == "grab-test.com"  # 原持有者不受影响
