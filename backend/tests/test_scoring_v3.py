"""意向分 V3（加分制主分）：PRD §五 MVP 口径的锚点校准用例。

锚点来自 spec §3.3——设计定稿时的判别力验证，改分值表必须同步改这里。
"""

from app.collectors.scoring import INTENT_SIGNALS, bonus_breakdown, grade_of, score_lead_inputs


def _items(total_items):
    return {it["key"]: it["points"] for it in total_items}


def test_grade_boundaries():
    assert grade_of(100) == "S"
    assert grade_of(80) == "S"
    assert grade_of(79) == "A"
    assert grade_of(60) == "A"
    assert grade_of(59) == "B"
    assert grade_of(40) == "B"
    assert grade_of(39) == "C"
    assert grade_of(0) == "C"


def test_anchor_ctwa_ecommerce_is_S():
    """PRD 范例（CTWA 跨境电商）：CTWA 40 + 官网WA 25 + 海外客服招聘 20 = 85 → S。"""
    score, items, grade = score_lead_inputs(
        fb_whatsapp=True,
        sources=[{"source": "meta_ads"}],
        whatsapp_hit=True,
        job_signals={"overseas_cs": {"label": "海外/英文客服", "points": 20}},
    )
    assert (score, grade) == (85, "S")
    assert _items(items) == {"ctwa_ad": 40, "site_whatsapp": 25, "overseas_cs_job": 20}


def test_anchor_site_private_dtc_is_A():
    """官网私域型 DTC：官网WA 25 + 出海 15 + 独立站 10 + 3国 10 + 社媒 5 = 65 → A。"""
    score, items, grade = score_lead_inputs(
        whatsapp_hit=True,
        website="https://dtc.example.com",
        overseas_signals={"currencies": ["USD"], "markets": ["US", "GB", "AE"]},
        target_countries=["US", "GB", "AE"],
        social={"facebook": "f", "instagram": "i"},
    )
    assert (score, grade) == (65, "A")


def test_anchor_wa_ops_midsize_is_B():
    """WA 运营在招中型：wa_ops 30 + crm 10 + 出海 15 = 55 → B。"""
    score, _, grade = score_lead_inputs(
        job_signals={
            "wa_ops": {"label": "WhatsApp 运营/客服", "points": 30},
            "crm_ops": {"label": "CRM/Customer Success 运营", "points": 12},
        },
        whatsapp_job=True,
        overseas_signals={"export_words": ["export"]},
    )
    assert (score, grade) == (55, "B")


def test_anchor_overseas_factory_no_wa_is_C():
    """有出海无 WA 的工厂：出海 15 + 独立站 10 = 25 → C（培育池）。"""
    score, _, grade = score_lead_inputs(
        website="https://factory.example.com",
        overseas_signals={"shipping": ["worldwide"]},
    )
    assert (score, grade) == (25, "C")


def test_anchor_bsp_migration_is_A():
    """竞品迁移型：wa_bsp 30 + 官网WA 25 + 出海 15 = 70 → A。"""
    score, items, grade = score_lead_inputs(
        saas_signals={"wa_bsp": 1},
        whatsapp_hit=True,
        overseas_signals={"languages": ["EN"]},
    )
    assert (score, grade) == (70, "A")
    assert _items(items)["wa_bsp_competitor"] == 30


def test_ctwa_and_meta_ads_mutually_exclusive():
    """CTWA（+40）成立时替代「在投广告」（+15），合计 40 而非 55。"""
    _, items, _ = score_lead_inputs(
        fb_whatsapp=True, sources=[{"source": "meta_ads"}]
    )
    assert _items(items) == {"ctwa_ad": 40}

    # FB 主页挂 wa.me 但没有任何在投证据 → 两者都不成立（CTWA 是组合信号）
    _, items, _ = score_lead_inputs(fb_whatsapp=True)
    assert "ctwa_ad" not in _items(items) and "meta_ads_running" not in _items(items)

    # 在投广告但主页无 wa.me → 只有 +15
    _, items, _ = score_lead_inputs(sources=[{"source": "meta_ads"}])
    assert _items(items) == {"meta_ads_running": 15}


def test_fb_whatsapp_without_ads_alone_scores_zero():
    """fb_whatsapp 单独存在（无在投证据）不给分——CTWA 是「私域+投放」组合信号。"""
    score, items, _ = score_lead_inputs(fb_whatsapp=True)
    assert score == 0 and items == []


def test_whatsapp_job_and_wa_ops_same_fact_counted_once():
    """whatsapp_job 列与 job_signals.wa_ops 是同一事实，只计一次 +30。"""
    score, items, _ = score_lead_inputs(
        whatsapp_job=True, job_signals={"wa_ops": {"label": "x", "points": 30}}
    )
    assert score == 30
    assert [it["key"] for it in items] == ["wa_ops_job"]


def test_saas_and_scale_signal_scopes():
    """SaaS 信号只经 saas_buying（+15，买入强度≥40 才触发）参与主分，不逐类累计；
    规模/联系人信号仍完全不进分。wa_bsp 是唯一逐项信号（+30）且不与 saas_buying 双计。
    （2026-09-01 深度复核改口径：此前 SaaS 全不进分 → 出海 SaaS 线买家永远 C 级）"""
    # 强度 62≥40 → 只有 +15，不逐类累计成 62
    score, items, _ = score_lead_inputs(
        saas_signals={"crm": 1, "helpdesk": 1, "chatbot": 1},
    )
    assert score == 15
    assert [it["key"] for it in items] == ["saas_buying"]
    # 弱 SaaS（18<40）不触发
    _, items2, _ = score_lead_inputs(saas_signals={"chatbot": 1})
    assert not items2
    # 规模/联系人仍不进分
    _, items3, _ = score_lead_inputs(
        job_urls=["j1", "j2", "j3", "j4"], email="x@a.com",
        contacts_count=3, has_tier1=True,
    )
    assert not items3


def test_total_caps_at_100():
    score, _, _ = score_lead_inputs(
        fb_whatsapp=True,
        sources=[{"source": "meta_ads"}],
        whatsapp_hit=True,
        whatsapp_job=True,
        saas_signals={"wa_bsp": 1},
        job_signals={
            "wa_ops": {"label": "x", "points": 30},
            "overseas_cs": {"label": "y", "points": 20},
            "crm_ops": {"label": "z", "points": 12},
        },
        wa_business=True,
        whatsapp_numbers=["1", "2"],
        website="https://a.com",
        overseas_signals={"markets": ["US", "GB", "AE"]},
        target_countries=["US", "GB", "AE"],
        social={"fb": "1", "ig": "2"},
    )
    assert score == 100


def test_intent_signal_table_values():
    """分值表 = spec §3.2 定稿值。改表必须过 spec，不许悄悄改。"""
    table = {k: p for k, _l, p in INTENT_SIGNALS}
    assert table == {
        "ctwa_ad": 40,
        "saas_buying": 15,
        "wa_ops_job": 30,
        "wa_bsp_competitor": 30,
        "site_whatsapp": 25,
        "overseas_cs_job": 20,
        "wa_business": 15,
        "meta_ads_running": 15,
        "overseas_biz": 15,
        "overseas_site": 10,
        "crm_job": 10,
        "three_markets": 10,
        "multi_numbers": 10,
        "social_active": 5,
    }


def test_anchor_saas_buying_line_visible():
    """F1（2026-09-01 深度复核）：定位句「WA 消息 + 出海 SaaS」两产品线——
    已在为 SaaS 付费（买入强度≥40）的出海公司此前 25 分 C 级永远进不了销售视野，
    saas_buying +15 让 SaaS 线买家到 B（可见）而不过 WA 主线（40 分封顶差异保留）。"""
    score, items, grade = score_lead_inputs(
        saas_signals={"crm": 1, "helpdesk": 1},  # 22+22=44 ≥40（brand_stack 12 也计）
        website="https://saasbuyer.example.com",
        overseas_signals={"languages": ["EN"]},
    )
    assert (score, grade) == (40, "B")

    # wa_bsp 不双计（已有 +30 专列信号）：wa_bsp+crm=52 但 ex_bsp=22<40 → 不加
    _, items2, _ = score_lead_inputs(saas_signals={"wa_bsp": 1, "crm": 1})
    assert "saas_buying" not in {it["key"] for it in items2}
    # brand_stack 进强度：brand_stack12+crm22+helpdesk22=56 ≥40
    _, items3, _ = score_lead_inputs(saas_signals={"brand_stack": 1, "crm": 1, "helpdesk": 1})
    assert "saas_buying" in {it["key"] for it in items3}


def test_bonus_breakdown_compat_wrapper():
    """历史迁移 f8a4c31e9d02 依赖的兼容接口：返回 v3 明细的同构 dict。"""
    bd = bonus_breakdown(
        fb_whatsapp=True, sources=[{"source": "meta_ads"}], whatsapp_hit=True
    )
    assert bd["total"] == 65
    assert {it["key"] for it in bd["items"]} == {"ctwa_ad", "site_whatsapp"}
    assert bonus_breakdown() == {"total": 0, "items": []}
