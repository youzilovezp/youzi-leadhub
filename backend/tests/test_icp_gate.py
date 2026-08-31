"""ICP 二重门（业务重构 2026-08-31）：资格判定 + 销售池门控。

共享测试库约束：本文件全部用 icpgate 前缀的唯一域名/行业，不与其他文件撞。
"""

from app.collectors.base import LeadDraft
from app.collectors.icp import compute_icp_status, is_non_buyer
from app.collectors.website_enrich import detect_icp_license
from app.crud.lead import upsert_lead


def test_detect_icp_license():
    """ICP 备案号识别：页脚常见格式（含省份简称与 -N 后缀）。"""
    html = '<footer>© 2026 <a href="https://beian.miit.gov.cn">粤ICP备2024123456号-2</a></footer>'
    assert detect_icp_license([html]) == "粤ICP备2024123456号-2"
    assert detect_icp_license(["<p>Contact us on WhatsApp</p>"]) is None
    assert detect_icp_license(["", None]) is None


def test_compute_icp_status_matrix():
    """四态判定：CN×出海 / CN 未出海 / 非CN有结论 / 证据不足。"""
    # qualified：CN 证据 + 出海信号
    assert (
        compute_icp_status(is_cn=True, overseas_signals={"markets": ["USA"]})
        == "qualified"
    )
    # 出海证据也认 FB 私域与投放国家
    assert compute_icp_status(is_cn=True, fb_whatsapp=True) == "qualified"
    assert compute_icp_status(is_cn=True, target_countries=["US"]) == "qualified"
    # 出海证据也认在招海外语义岗（2026-08-31 巡检：jobui 通道无官网企业
    # 靠 overseas_cs/social_ops/wa_ops 进销售池；crm_ops 不算——不必然出海）
    assert (
        compute_icp_status(is_cn=True, job_signals={"overseas_cs": {"label": "海外客服", "points": 20}})
        == "qualified"
    )
    assert (
        compute_icp_status(is_cn=True, job_signals={"social_ops": {"label": "海外社媒运营", "points": 15}})
        == "qualified"
    )
    assert (
        compute_icp_status(is_cn=True, job_signals={"wa_ops": {"label": "WhatsApp 运营", "points": 30}})
        == "qualified"
    )
    assert (
        compute_icp_status(is_cn=True, job_signals={"crm_ops": {"label": "CRM 运营", "points": 12}})
        == "cn_domestic"
    )
    # cn_domestic：CN 证据但无出海信号
    assert compute_icp_status(is_cn=True) == "cn_domestic"
    # CN 证据兜底：国家码 CN / +86 号码
    assert compute_icp_status(country="CN") == "cn_domestic"
    assert compute_icp_status(phone_e164="+8613800138000") == "cn_domestic"
    # foreign：非 CN 且有评估结论（已富化 / meta_ads 来源且有官网做过中文判定）
    assert compute_icp_status(enriched_at=object()) == "foreign"
    assert (
        compute_icp_status(sources=[{"source": "meta_ads"}], website="https://x.com")
        == "foreign"
    )
    # 2026-08-31 审计修正：meta_ads 无官网（探测失败/登录墙）= 没有富化翻案通道，
    # 保持 unknown 不做有罪推定——英文品牌中国大卖不得被不可见地丢弃
    assert compute_icp_status(sources=[{"source": "meta_ads"}]) == "unknown"
    # unknown：非 CN 但从未评估（不做有罪推定）
    assert compute_icp_status() == "unknown"
    assert compute_icp_status(sources=[{"source": "manual"}]) == "unknown"


async def test_icp_gate_list_and_stats(client, admin_credentials, db_session):
    """门控口径：默认列表排除 foreign；icp=foreign/all 可显式选出；stats 有分布。"""
    r = await client.post(
        "/api/v1/auth/login",
        json=admin_credentials,
    )
    h = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}

    # 1) 手工录入中国企业（country=CN）：无出海证据 → cn_domestic，默认列表可见
    r = await client.post(
        "/api/v1/collect/leads",
        headers=h,
        json={
            "name": "ICP门科技（深圳）有限公司",
            "country": "CN",
            "website": "https://icpgate-cn.com",
        },
    )
    cn_id = r.json()["data"]["id"]
    assert r.json()["data"]["icp_status"] == "cn_domestic"

    # 2) 直插外国企业（meta_ads 来源、纯英文页判定）：→ foreign
    foreign, _ = await upsert_lead(
        db_session,
        LeadDraft(
            source="meta_ads",
            name="ICPGATE Foreign Commerce Ltd",
            country="US",
            website="https://icpgate-foreign.com",
        ),
    )
    await db_session.commit()
    assert foreign.icp_status == "foreign"

    # 默认列表（按 keyword=icpgate 圈定本文件数据）：只见 CN 家
    r = await client.get("/api/v1/collect/leads?keyword=icpgate", headers=h)
    names = [x["name"] for x in r.json()["data"]["items"]]
    assert any("ICP门科技" in n for n in names)
    assert not any("Foreign Commerce" in n for n in names)

    # icp=foreign：显式选出外国企业
    r = await client.get("/api/v1/collect/leads?keyword=icpgate&icp=foreign", headers=h)
    names = [x["name"] for x in r.json()["data"]["items"]]
    assert any("Foreign Commerce" in n for n in names)
    assert not any("ICP门科技" in n for n in names)

    # icp=all：不过滤
    r = await client.get("/api/v1/collect/leads?keyword=icpgate&icp=all", headers=h)
    names = [x["name"] for x in r.json()["data"]["items"]]
    assert any("Foreign Commerce" in n for n in names)
    assert any("ICP门科技" in n for n in names)

    # stats：五态分布存在且 foreign 计入（non_buyer 2026-08-31 第五态）
    r = await client.get("/api/v1/collect/stats", headers=h)
    icp_counts = r.json()["data"]["icp_counts"]
    assert set(icp_counts) == {"qualified", "cn_domestic", "foreign", "non_buyer", "unknown"}
    assert icp_counts["foreign"] >= 1

    # 清理（共享测试库）
    await client.delete(f"/api/v1/collect/leads/{cn_id}", headers=h)
    await db_session.delete(foreign)
    await db_session.commit()


def test_non_buyer_blacklist_domains():
    """实测漏网的行业媒体/社区/平台门户域（2026-08-31 dev 库查实清单）。"""
    for domain in (
        "ikjzd.com",        # 跨境知道（资讯）
        "wearesellers.com", # 知无不言（社区）
        "cifnews.com",      # 雨果跨境（媒体/平台）
        "kuajingyan.com",   # 跨境眼
        "kjtong.com",       # 跨境通
        "mckinsey.com.cn",  # 咨询报告页
        "www.ikjzd.com",    # 子域同样命中
    ):
        assert is_non_buyer(domain=domain), domain


def test_non_buyer_name_patterns():
    """名称词表：媒体/社区/报告/下载形态不是买家。"""
    for name in (
        "跨境知道-看跨境电商平台资讯、查报告、找资源",
        "知无不言跨境电商社区",
        "中国跨境电商市场研究白皮书",
        "Download WhatsApp (free) for Windows",
    ):
        assert is_non_buyer(name=name), name
    # 正常目标企业不得误杀
    for name in ("安克创新科技股份有限公司", "深圳市某跨境电子商务有限公司", "SHEIN"):
        assert not is_non_buyer(name=name), name


def test_icp_status_non_buyer_precedes_qualified():
    """黑名单优先于 CN/出海证据：媒体站哪怕 CN+出海全占也不进销售池。"""
    status = compute_icp_status(
        name="知无不言跨境电商社区",
        domain="wearesellers.com",
        is_cn=True,
        country="CN",
        phone_e164="+8613800138000",
        overseas_signals={"languages": ["EN"]},
        enriched_at="2026-08-31T00:00:00+00:00",
    )
    assert status == "non_buyer"


def test_normal_buyer_unaffected():
    status = compute_icp_status(
        name="安克创新科技股份有限公司",
        domain="anker.com",
        is_cn=True,
        country="CN",
        overseas_signals={"languages": ["EN"]},
    )
    assert status == "qualified"


def test_industry_group_mapping():
    from app.collectors.industry_labels import industry_group_of

    assert industry_group_of("电商", "深圳市安克创新科技股份有限公司") == "cross_border_ecom"
    assert industry_group_of(None, "某游戏网络科技有限公司") == "game_app"
    assert industry_group_of("广告公司", None) == "overseas_service"
    assert industry_group_of(None, "某餐饮管理有限公司") == ""


def test_non_buyer_letschuhai_and_36kr():
    """2026-09-01 实测漏网：36氪出海独立域 + 名称 token（36kr.com 在清单、letschuhai.com 漏）。"""
    assert is_non_buyer(domain="letschuhai.com")
    assert is_non_buyer(name="36氪出海")
    assert compute_icp_status(
        name="36氪出海", domain="letschuhai.com", is_cn=True, country="CN",
        overseas_signals={"export_words": ["出海"]},
    ) == "non_buyer"
