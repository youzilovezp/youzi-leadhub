"""可靠性修复测试（2026-08-31 审计批次4）。"""

import pytest

# ---------- overseas：正文/属性分层 + market re.I ----------


def test_overseas_no_false_positive_from_script():
    """JS 变量名（script 里的 international_shipping）不再触发 shipping 误报。"""
    from app.collectors.overseas import detect_overseas_signals

    html = """
    <html><head><script>
      var config = { international_shipping: false, usa: null };
      // worldwide shipping feature flag
    </script></head><body><p>Welcome to Acme.</p></body></html>
    """
    sig = detect_overseas_signals([html])
    assert "shipping" not in sig
    assert "markets" not in sig


def test_overseas_visible_text_still_hits():
    """可见正文里的出海表达正常命中（国名大写才算，代词 us 不算）。"""
    from app.collectors.overseas import detect_overseas_signals

    html = (
        "<html><body><p>We provide international shipping."
        " Trusted by customers in USA and UK.</p></body></html>"
    )
    sig = detect_overseas_signals([html])
    assert sig.get("shipping")
    assert {"US", "GB"} <= set(sig.get("markets", []))


def test_overseas_us_pronoun_not_market():
    """「contact us」的 us 是代词不是美国市场（re.I 教训回归测试）。"""
    from app.collectors.overseas import detect_overseas_signals

    html = "<html><body><p>Contact us or email us for support.</p></body></html>"
    sig = detect_overseas_signals([html])
    assert "markets" not in sig


def test_overseas_attribute_signals_from_raw_html():
    """hreflang / 电商栈指纹靠属性与资源 URL，剥标签后仍必须命中（raw 层）。"""
    from app.collectors.overseas import detect_overseas_signals

    html = """
    <html><head>
    <link rel="alternate" hreflang="en" href="/en"><link rel="alternate" hreflang="de" href="/de">
    <script src="https://cdn.shopify.com/s/theme.js"></script>
    </head><body><p>Shop now</p></body></html>
    """
    sig = detect_overseas_signals([html])
    assert set(sig.get("languages", [])) >= {"en", "de"}
    assert "shopify" in sig.get("ecommerce", [])


# ---------- seniority 分层误报 ----------


def test_seniority_product_owner_not_tier1():
    """「Product Owner」是中层敏捷角色，不是决策层。"""
    from app.crud.contact import derive_seniority

    assert derive_seniority("Product Owner") == "unknown"
    assert derive_seniority("Business Owner") == "tier1"
    assert derive_seniority("CEO & Founder") == "tier1"
    assert derive_seniority("总经理") == "tier1"


def test_seniority_vice_president_tier2():
    """Vice President 不吃 president 关键词 → 归 tier2（仍有分量）。"""
    from app.crud.contact import derive_seniority

    assert derive_seniority("Vice President of Marketing") == "tier2"
    assert derive_seniority("President") == "tier1"


# ---------- 质检 contact 池含电话联系人 ----------


@pytest.mark.asyncio
async def test_quality_contact_pool_includes_phone_only(client):
    """只有电话（WA 号码）联系人的线索也进 contact 抽检池。

    直接断言池条件（端点的随机队列在全量跑时受共享库前序数据挤压，
    50 上限内不保证出队——端点级行为由 test_quality_review 的 step3 覆盖）。
    """
    from sqlalchemy import select

    from app.api.v1.endpoints.quality import _pool_conditions  # noqa: SLF001
    from app.db.session import async_session
    from app.models.lead import Lead, LeadContact

    async with async_session() as s:
        lead = Lead(name="电话联系人质检公司", dedupe_key="namecity:qc-phone")
        s.add(lead)
        await s.flush()
        s.add(LeadContact(lead_id=lead.id, phone="+60111222333", confidence=85, source="website_enrich"))
        await s.commit()
        lead_id = lead.id

    async with async_session() as s:
        in_pool = (
            await s.execute(select(Lead.id).where(Lead.id == lead_id, *_pool_conditions("contact")))
        ).scalar_one_or_none()
    assert in_pool == lead_id


# ---------- 富化排除终态线索 ----------


@pytest.mark.asyncio
async def test_load_scope_excludes_terminal_status(client):
    """全库富化不重抓 won/invalid；从未跟进（NULL）必须保留。"""
    from app.collectors.website_enrich import _load_scope  # noqa: SLF001
    from app.db.session import async_session
    from app.models.lead import Lead

    async with async_session() as s:
        for name, status in (
            ("富化范围-正常A", None),
            ("富化范围-成交", "won"),
            ("富化范围-无效", "invalid"),
            ("富化范围-培育", "paused"),
        ):
            slug = {"富化范围-正常A": "a", "富化范围-成交": "won", "富化范围-无效": "inv", "富化范围-培育": "paused"}[name]
            s.add(
                Lead(
                    name=name,
                    website=f"https://scope-{slug}.com",
                    domain=f"scope-{slug}.com",
                    follow_status=status,
                    dedupe_key=f"namecity:scope-{slug}",
                )
            )
        await s.commit()

    async with async_session() as s:
        rows = await _load_scope(s, [])
    names = {r[0] for r in rows}  # 返回 (id, website)，用 id 断言不出错——改为查名字
    ids = rows
    # 再核对：终态两条不应出现
    from sqlalchemy import select

    async with async_session() as s:
        all_rows = (
            await s.execute(select(Lead.id, Lead.name, Lead.follow_status).where(Lead.name.like("富化范围-%")))
        ).all()
    by_id = {r[0]: (r[1], r[2]) for r in all_rows}
    scope_ids = {r[0] for r in ids}
    for lid, (name, status) in by_id.items():
        if status in ("won", "invalid"):
            assert lid not in scope_ids, f"{name}({status}) 不应进富化范围"
        else:
            assert lid in scope_ids, f"{name}({status}) 应保留在富化范围"
    _ = names  # noqa: B018  上面的占位变量仅用于可读性


# ---------- CSV 导入行数上限 ----------


@pytest.mark.asyncio
async def test_import_rejects_oversized_csv(client, admin_credentials):
    login = await client.post("/api/v1/auth/login", json=admin_credentials)
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    csv_text = "name\n" + "\n".join(f"公司{i}" for i in range(5001))
    r = await client.post("/api/v1/collect/leads/import", headers=headers, json={"csv_text": csv_text})
    assert r.status_code == 400
    assert r.json()["code"] == 40001
    assert "5000" in r.json()["message"]


# ---------- meta_ads 韩文豁免 ----------


def test_looks_cn_excludes_hangul():
    from app.collectors.meta_ad_library import _looks_cn  # noqa: SLF001

    assert _looks_cn(["跨境小铺 CrossBorder"]) is True
    assert _looks_cn(["山田商事株式会社"]) is True  # 纯汉字无假名：保持中文判定
    assert _looks_cn(["삼성무역 상사"]) is False  # 谚文 → 韩文
    assert _looks_cn(["ヤマト商事"]) is False  # 假名 → 日文
