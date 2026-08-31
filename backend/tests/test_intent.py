"""三问生成器：为什么需要你 / 应该卖什么 / 应该找谁（PRD 核心价值的直接实现）。"""


class _C:  # 最小联系人桩（属性访问与 ORM LeadContact 同构）
    def __init__(self, name, title, seniority, email=None):
        self.name, self.job_title, self.seniority, self.email = name, title, seniority, email


class _L:  # 最小 Lead 桩
    def __init__(self, **kw):
        base = {
            "name": "测试公司", "whatsapp_hit": False, "whatsapp_url": None, "whatsapp_job": False,
            "whatsapp_numbers": [], "saas_signals": {}, "scenes": [], "sources": [],
            "industry": None, "score_breakdown": {}, "job_signals": {}, "fb_whatsapp": False,
            "email": None, "website": None,
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
    assert tq["why"][1]["evidence_url"] == "https://wa.me/8613800138000"  # site_whatsapp → whatsapp_url


def test_what_aggregates_products_needs_scenes():
    from app.collectors.intent import build_three_questions

    lead = _L(
        whatsapp_hit=True, whatsapp_url="https://wa.me/8613", whatsapp_job=True,
        scenes=["customer_service", "marketing"],
        saas_signals={"crm": 1, "helpdesk": 1},
        sources=[{"source": "meta_ads"}],
        score_breakdown={"total": 40, "items": [{"key": "site_whatsapp", "label": "官网 WhatsApp 入口", "points": 25}]},
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
        whatsapp_hit=True, whatsapp_url="https://wa.me/8613", whatsapp_job=True,
        score_breakdown={"total": 55, "items": [
            {"key": "site_whatsapp", "label": "官网 WhatsApp 入口", "points": 25},
            {"key": "wa_ops_job", "label": "在招 WhatsApp 运营岗", "points": 30},
        ]},
    )
    assert build_three_questions(strong)["complete"] is True

    # 单信号无产品 → 不齐备
    weak = _L(
        overseas_signals={"shipping": ["worldwide"]},
        score_breakdown={"total": 15, "items": [{"key": "overseas_biz", "label": "出海业务证据", "points": 15}]},
    )
    assert build_three_questions(weak)["complete"] is False
