"""产品推荐 & 销售建议规则。"""

from app.collectors.recommend import recommend_products, sales_suggestion


def test_rule_wa_customer_service():
    recs = recommend_products(
        whatsapp_hit=True,
        whatsapp_url="https://wa.me/60123456789",
        whatsapp_job=True,
        scenes=["customer_service"],
        saas_signals={},
    )
    assert [r["key"] for r in recs] == ["wa_cs"]
    assert recs[0]["priority"] == 1


def test_rule_marketing_message_needs_ecommerce_or_transactional():
    # 营销场景 + 电商行业 → 命中
    recs = recommend_products(
        whatsapp_hit=True,
        whatsapp_url="https://wa.me/60",
        whatsapp_job=False,
        scenes=["marketing"],
        saas_signals={},
        industry="跨境电商",
    )
    assert "marketing_message" in [r["key"] for r in recs]
    # 营销场景 + 交易场景 → 命中
    recs = recommend_products(
        whatsapp_hit=True,
        whatsapp_url="https://wa.me/60",
        whatsapp_job=False,
        scenes=["marketing", "transactional"],
        saas_signals={},
        industry="dental",
    )
    keys = [r["key"] for r in recs]
    assert "marketing_message" in keys and "transactional_message" in keys


def test_rule_ai_cs_needs_saas_strength():
    """v3：SaaS 买入强度内部计算（SAAS_CATEGORY_POINTS 求和 ≥40 才推 AI 客服）。"""
    recs = recommend_products(
        whatsapp_hit=True,
        whatsapp_url=None,
        whatsapp_job=False,
        scenes=["customer_service"],
        saas_signals={"ai_service": 1, "crm": 1},  # 18+22=40
    )
    assert "ai_cs" in [r["key"] for r in recs]
    # 强度不够 → 不推
    recs = recommend_products(
        whatsapp_hit=True,
        whatsapp_url=None,
        whatsapp_job=False,
        scenes=["customer_service"],
        saas_signals={"ai_service": 1},  # 只有 ai_service 18 分
    )
    assert "ai_cs" not in [r["key"] for r in recs]


def test_no_whatsapp_no_products():
    recs = recommend_products(
        whatsapp_hit=False,
        whatsapp_url=None,
        whatsapp_job=False,
        scenes=["marketing", "transactional"],
        saas_signals={"crm": 1},
    )
    assert recs == []


def test_sales_suggestion_elements():
    recs = [
        {"key": "wa_cs", "name": "WhatsApp 客服 SaaS", "reason": "已检测到使用痕迹，且存在客服场景", "priority": 1}
    ]
    text = sales_suggestion(
        grade="A",
        whatsapp_url="https://wa.me/60",
        whatsapp_job=False,
        saas_signals={"crm": 1},
        has_tier1_contact=True,
        products=recs,
    )
    assert "A 级" in text
    assert "WhatsApp 入口" in text
    assert "CRM" in text
    assert "决策层" in text
    assert "WhatsApp 客服 SaaS" in text


def test_sales_suggestion_grade_copy():
    assert "当天跟进" in sales_suggestion(grade="S", whatsapp_url=None, whatsapp_job=False, saas_signals={})
    assert "培育池" in sales_suggestion(grade="B", whatsapp_url=None, whatsapp_job=False, saas_signals={})
    assert "暂不优先" in sales_suggestion(grade="C", whatsapp_url=None, whatsapp_job=False, saas_signals={})
    # 在招岗位也有对应文案
    assert "在招 WhatsApp" in sales_suggestion(
        grade="C", whatsapp_url=None, whatsapp_job=True, saas_signals={}
    )


def test_rule_ads_agency_line():
    """广告代理线（双业务线）：在投 Meta 广告即推荐，与 WA 使用无关。"""
    recs = recommend_products(
        whatsapp_hit=False,
        whatsapp_url=None,
        whatsapp_job=False,
        scenes=[],
        saas_signals={},
        sources=[{"source": "meta_ads"}],
    )
    assert [r["key"] for r in recs] == ["ads_agency"]
    # 无 WA 且无广告 → 空推荐
    recs = recommend_products(
        whatsapp_hit=False,
        whatsapp_url=None,
        whatsapp_job=False,
        scenes=[],
        saas_signals={},
        sources=[{"source": "web_search"}],
    )
    assert recs == []


def test_rule_overseas_saas_line():
    """出海 SaaS 线：SaaS 需求信号成规模（≥2 类）即推荐 SaaS 方案。"""
    recs = recommend_products(
        whatsapp_hit=False,
        whatsapp_url=None,
        whatsapp_job=False,
        scenes=[],
        saas_signals={"crm": 1, "helpdesk": 1},
    )
    keys = [r["key"] for r in recs]
    assert "overseas_saas" in keys
    # 单一弱信号不推 SaaS 方案（避免噪推）
    recs = recommend_products(
        whatsapp_hit=False,
        whatsapp_url=None,
        whatsapp_job=False,
        scenes=[],
        saas_signals={"crm": 1},
    )
    assert "overseas_saas" not in [r["key"] for r in recs]


def test_recommend_products_no_dim_saas_param():
    """v3 后 SaaS 强度内部计算：单类 crm 不触发 SaaS 方案，两类触发（原 dim_saas>=40 口径）。"""
    one = recommend_products(
        whatsapp_hit=False, whatsapp_url=None, whatsapp_job=False,
        scenes=[], saas_signals={"crm": 1},
    )
    assert all(r["key"] != "overseas_saas" for r in one)
    two = recommend_products(
        whatsapp_hit=False, whatsapp_url=None, whatsapp_job=False,
        scenes=[], saas_signals={"crm": 1, "helpdesk": 1},
    )
    assert any(r["key"] == "overseas_saas" for r in two)
    # wa_bsp 一类即 30 分强度（>=40 不满足）→ 不触发；wa_bsp+crm 满足
    bsp_only = recommend_products(
        whatsapp_hit=False, whatsapp_url=None, whatsapp_job=False,
        scenes=[], saas_signals={"wa_bsp": 1},
    )
    assert all(r["key"] != "overseas_saas" for r in bsp_only)
