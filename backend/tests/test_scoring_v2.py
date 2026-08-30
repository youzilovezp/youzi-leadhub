"""六维评分 V2：分级边界 / 维度封顶 / 权重覆盖 / 旗舰校准用例。"""

from app.collectors.scoring import (
    DIM_WEIGHTS,
    apply_score,
    effective_dim_weights,
    grade_of,
    score_lead_inputs,
)
from app.core.config import settings


def test_grade_boundaries():
    assert grade_of(100) == "S"
    assert grade_of(80) == "S"
    assert grade_of(79) == "A"
    assert grade_of(60) == "A"
    assert grade_of(59) == "B"
    assert grade_of(40) == "B"
    assert grade_of(39) == "C"
    assert grade_of(0) == "C"


def test_saas_dimension_caps_at_100():
    """全量 SaaS 信号命中原始分 115（22+22+18+18+12+8+场景15），封顶 100。"""
    _, dims, _ = score_lead_inputs(
        saas_signals={
            "crm": 1, "helpdesk": 1, "chatbot": 1,
            "ai_service": 1, "marketing_automation": 1, "omnichannel": 1,
        },
        scenes=["saas"],
    )
    assert dims["saas"] == 100


def test_contact_dimension_tiers():
    """tier1 决策人拉满：1 人 30 + tier1 40 = 70。"""
    _, dims, _ = score_lead_inputs(contacts_count=1, has_tier1=True)
    assert dims["contact"] == 70
    _, dims, _ = score_lead_inputs(contacts_count=2, has_tier2=True)
    assert dims["contact"] == 70  # 50 + 20
    _, dims, _ = score_lead_inputs(contacts_count=0, email="x@a.com")
    assert dims["contact"] == 10


def test_dim_weights_env_override_normalizes(monkeypatch):
    """SCORING_DIM_WEIGHTS 按键覆盖后按和归一化（合计恰 100，残差补最大维度）。"""
    monkeypatch.setattr(settings, "SCORING_DIM_WEIGHTS", {"overseas": 50, "whatsapp": 50})
    weights = effective_dim_weights()
    assert sum(weights.values()) == 100
    # 覆盖后 50/50/20/10/10/5（和 145）→ 归一化 34/34/14/7/7/3，残差补最大维度
    assert abs(weights["overseas"] - weights["whatsapp"]) <= 1
    assert weights["overseas"] > weights["saas"] > weights["contact"]


def test_default_weights_sum_to_100():
    assert sum(DIM_WEIGHTS.values()) == 100


def test_flagship_icp_profile():
    """旗舰 ICP（中国出海跨境电商全证据）：63/A；+CRM 信号 68/A；+决策联系人+更多岗位 81/S。

    2026-08-31 口径修正后：is_cn 不再进出海维（资格由 ICP 二重门承担），
    出海维=出海深度（FB私域30+官网10+≥3国10+信号类×7）。
    """
    base: dict = dict(  # noqa: C408 - kwargs 语义直观
        is_cn=True,
        fb_whatsapp=True,
        country="CN",
        website="https://acme.com",
        whatsapp_hit=True,
        whatsapp_url="https://wa.me/8613800138000",
        whatsapp_job=True,
        scenes=["customer_service"],
        social={"facebook": "f", "instagram": "i"},
        email="x@acme.com",
        phone_e164="+8613800138000",
        sources=[{"source": "meta_ads"}, {"source": "web_search"}],
        target_countries=["US", "GB", "AE"],
        overseas_signals={
            "currencies": ["USD"], "languages": ["EN"], "ecommerce": ["shopify"],
            "markets": ["USA"], "shipping": ["worldwide"],
        },
    )
    score, dims, grade = score_lead_inputs(**base)
    assert (score, grade) == (63, "A")
    assert dims == {"overseas": 85, "whatsapp": 100, "saas": 0, "scale": 50, "marketing": 65, "contact": 10}

    # + CRM 信号 → SaaS 维 22 → 总分 63+4.4 → 68
    score, _, grade = score_lead_inputs(**base, saas_signals={"crm": 1})
    assert (score, grade) == (68, "A")

    # + helpdesk + tier1 联系人 + 4 个在招岗位 + 第三个来源 → 81/S
    # （SaaS 维 crm22+helpdesk22=44，未带 saas 场景）
    score, dims, grade = score_lead_inputs(
        **{**base, "sources": [{"source": "meta_ads"}, {"source": "web_search"},
                               {"source": "job_posting"}]},
        saas_signals={"crm": 1, "helpdesk": 1},
        job_urls=["j1", "j2", "j3", "j4"],
        contacts_count=1,
        has_tier1=True,
    )
    assert (score, grade) == (81, "S")
    assert dims["scale"] == 100 and dims["marketing"] == 75 and dims["contact"] == 70


def test_multi_whatsapp_numbers_boost():
    """多分线号码（≥2）= 规模化私域证据，WhatsApp 维 +10（§4.1）。"""
    _, one, _ = score_lead_inputs(whatsapp_hit=True, whatsapp_url="https://wa.me/60")
    _, two, _ = score_lead_inputs(
        whatsapp_hit=True, whatsapp_url="https://wa.me/60", whatsapp_numbers=["601", "602"]
    )
    assert two["whatsapp"] == one["whatsapp"] + 10


def test_apply_score_writes_orm_fields():
    class FakeLead:
        is_cn = True
        fb_whatsapp = False
        country = "MY"
        website = "https://a.com"
        whatsapp_hit = False
        whatsapp_url = None
        whatsapp_job = False
        scenes = []
        whatsapp_numbers = []
        saas_signals = {}
        job_urls = []
        social = {}
        email = None
        phone_raw = None
        phone_e164 = None
        sources = []
        score = 0
        score_signals = {}
        grade = "C"

    lead = FakeLead()
    old, new, grade = apply_score(lead)
    assert old == 0
    assert lead.score == new
    assert lead.grade == grade == "C"
    assert set(lead.score_signals) == set(DIM_WEIGHTS)
