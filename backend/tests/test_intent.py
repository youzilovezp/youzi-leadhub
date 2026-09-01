"""三问生成器：为什么需要你 / 应该卖什么 / 应该找谁（PRD 核心价值的直接实现）。"""


class _C:  # 最小联系人桩（属性访问与 ORM LeadContact 同构）
    def __init__(self, name, title, seniority, email=None):
        self.name, self.job_title, self.seniority, self.email = name, title, seniority, email


class _L:  # 最小 Lead 桩
    def __init__(self, **kw):
        base = {
            "name": "测试公司",
            "whatsapp_hit": False,
            "whatsapp_url": None,
            "whatsapp_job": False,
            "whatsapp_numbers": [],
            "saas_signals": {},
            "scenes": [],
            "sources": [],
            "industry": None,
            "score_breakdown": {},
            "job_signals": {},
            "fb_whatsapp": False,
            "email": None,
            "website": None,
        }
        base.update(kw)
        for k, v in base.items():
            setattr(self, k, v)


def test_why_top3_by_points_with_evidence():
    from app.collectors.intent import build_three_questions

    lead = _L(
        whatsapp_hit=True,
        whatsapp_url="https://wa.me/8613800138000",
        whatsapp_job=True,
        job_signals={
            "wa_ops": {"label": "WhatsApp 运营/客服", "points": 30},
            "overseas_cs": {"label": "海外/英文客服", "points": 20},
            "crm_ops": {"label": "CRM/Customer Success 运营", "points": 12},
        },
        overseas_signals={"languages": ["EN"]},
        website="https://x.com",
        score_breakdown={
            "total": 80,
            "items": [
                {"key": "site_whatsapp", "label": "官网 WhatsApp 入口", "points": 25},
                {"key": "wa_ops_job", "label": "在招 WhatsApp 运营岗", "points": 30},
                {"key": "overseas_cs_job", "label": "在招海外/英文客服岗", "points": 20},
                {"key": "crm_job", "label": "在招 CRM/客服系统运营岗", "points": 10},
            ],
        },
    )
    tq = build_three_questions(lead)
    assert [w["key"] for w in tq["why"]] == ["wa_ops_job", "site_whatsapp", "overseas_cs_job"]
    assert (
        tq["why"][1]["evidence_url"] == "https://wa.me/8613800138000"
    )  # site_whatsapp → whatsapp_url


def test_why_evidence_url_covers_all_signal_keys():
    """FR-4「每 1 分可溯源」：14 信号键全部能给出证据 URL（行属性或信号链），
    不再只有 7/14——wa_bsp_competitor(+30) 常驻 top-3 却无 URL 是可点开核对落空。"""
    from app.collectors.intent import _evidence_url

    lead = _L(
        website="https://x.com",
        whatsapp_url="https://wa.me/86137",
        job_urls=["https://x.com/jobs"],
    )
    for key in (
        "ctwa_ad",
        "wa_ops_job",
        "wa_bsp_competitor",
        "site_whatsapp",
        "overseas_cs_job",
        "wa_business",
        "meta_ads_running",
        "overseas_biz",
        "saas_buying",
        "overseas_site",
        "crm_job",
        "three_markets",
        "multi_numbers",
        "social_active",
    ):
        assert _evidence_url(lead, key) is not None, key


def test_why_evidence_url_prefers_signal_chain():
    """信号证据链优先：meta_ads_running 的真实证据是 FB 主页 URI（lead_signals），
    ctwa_ad 的 fb_whatsapp 证据同源；行属性 website 只是回退。"""
    from app.collectors.intent import _evidence_url

    lead = _L(website="https://x.com")
    sig = {
        "meta_ad": "https://facebook.com/pages/12345",
        "fb_whatsapp": "https://facebook.com/pages/999",
    }
    assert _evidence_url(lead, "meta_ads_running", sig) == "https://facebook.com/pages/12345"
    assert _evidence_url(lead, "ctwa_ad", sig) == "https://facebook.com/pages/999"
    # 无信号链时回退行属性
    assert _evidence_url(lead, "meta_ads_running") == "https://x.com"


def test_what_aggregates_products_needs_scenes():
    from app.collectors.intent import build_three_questions

    lead = _L(
        whatsapp_hit=True,
        whatsapp_url="https://wa.me/8613",
        whatsapp_job=True,
        scenes=["customer_service", "marketing"],
        saas_signals={"crm": 1, "helpdesk": 1},
        sources=[{"source": "meta_ads"}],
        score_breakdown={
            "total": 40,
            "items": [{"key": "site_whatsapp", "label": "官网 WhatsApp 入口", "points": 25}],
        },
    )
    tq = build_three_questions(lead)
    assert any(p["key"] == "wa_cs" for p in tq["what"]["products"])
    assert "ads" in [n["type"] for n in tq["what"]["need_types"]]
    assert tq["what"]["scenes"] == ["客服", "营销"]
    assert "CRM" in tq["what"]["saas_signals"]


def test_who_contacts_first_then_wa_numbers_then_roles():
    from app.collectors.intent import build_three_questions

    lead = _L(whatsapp_numbers=["8613900000001", "8613900000002"])
    tq = build_three_questions(lead, contacts=[_C("张三", "海外客服主管", "tier2", "z@a.com")])
    assert tq["who"]["contacts"][0]["name"] == "张三"
    assert tq["who"]["whatsapp_numbers"] == ["8613900000001", "8613900000002"]

    # 无联系人：按信号派生角色——在招海外客服 → 海外客服负责人
    lead2 = _L(job_signals={"overseas_cs": {"label": "海外/英文客服", "points": 20}})
    tq2 = build_three_questions(lead2)
    roles = [r["role"] for r in tq2["who"]["roles"]]
    assert "海外客服负责人" in roles


def test_who_role_never_empty():
    from app.collectors.intent import build_three_questions

    tq = build_three_questions(_L())  # 啥都没有
    assert tq["who"]["roles"], "兜底角色必须存在（海外业务负责人）"
    assert tq["who"]["roles"][-1]["role"] == "海外业务负责人"


def test_completeness_rule():
    from app.collectors.intent import build_three_questions

    # 信号≥2 + 产品≥1 + who 兜底 → complete
    strong = _L(
        whatsapp_hit=True,
        whatsapp_url="https://wa.me/8613",
        whatsapp_job=True,
        score_breakdown={
            "total": 55,
            "items": [
                {"key": "site_whatsapp", "label": "官网 WhatsApp 入口", "points": 25},
                {"key": "wa_ops_job", "label": "在招 WhatsApp 运营岗", "points": 30},
            ],
        },
    )
    assert build_three_questions(strong)["complete"] is True

    # 单信号无产品 → 不齐备
    weak = _L(
        overseas_signals={"shipping": ["worldwide"]},
        score_breakdown={
            "total": 15,
            "items": [{"key": "overseas_biz", "label": "出海业务证据", "points": 15}],
        },
    )
    assert build_three_questions(weak)["complete"] is False


def test_roles_do_not_satisfy_who():
    from app.collectors.intent import build_three_questions

    # why≥2 + 产品≥1，但无联系人/无号码/无 whatsapp_url：
    # roles 再多也只是「该找谁的角色」，不是建联入口 → 不齐备（spec §六头号定义）
    lead = _L(
        whatsapp_hit=True,
        whatsapp_url=None,
        whatsapp_job=True,
        score_breakdown={
            "total": 55,
            "items": [
                {"key": "site_whatsapp", "label": "官网 WhatsApp 入口", "points": 25},
                {"key": "wa_ops_job", "label": "在招 WhatsApp 运营岗", "points": 30},
            ],
        },
    )
    tq = build_three_questions(lead)
    assert len(tq["why"]) >= 2
    assert tq["what"]["products"], "前置：产品已命中（wa_cs）"
    assert tq["who"]["roles"], "前置：兜底角色存在"
    assert not (tq["who"]["contacts"] or tq["who"]["whatsapp_numbers"] or tq["who"]["whatsapp_url"])
    assert tq["complete"] is False


def test_top_contacts_tier_order_and_truncation():
    from app.collectors.intent import build_three_questions

    lead = _L()
    contacts = [
        _C("甲", "客服专员", "tier3"),
        _C("乙", "海外客服总监", "tier1"),
        _C("丙", "客服经理", "tier2"),
        _C("丁", "客服专员", "tier3"),
    ]
    tq = build_three_questions(lead, contacts=contacts)
    out = tq["who"]["contacts"]
    assert len(out) == 3, "最多 3 个联系人"
    assert [c["seniority"] for c in out] == ["tier1", "tier2", "tier3"]
    assert [c["name"] for c in out] == ["乙", "丙", "甲"]  # tier3 并列取先来者


def test_fb_whatsapp_role_fires():
    from app.collectors.intent import build_three_questions

    # fb_whatsapp 是 Lead 布尔列而非 job/saas 信号键——必须读列值才能触发角色
    lead = _L(fb_whatsapp=True)
    roles = [r["role"] for r in build_three_questions(lead)["who"]["roles"]]
    assert "海外营销负责人" in roles
    assert roles[0] == "海外营销负责人", "派生角色在兜底位之前"
    assert roles[-1] == "海外业务负责人"  # 兜底角色仍在最后
