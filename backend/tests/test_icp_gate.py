"""ICP 二重门（业务重构 2026-08-31）：资格判定 + 销售池门控。

共享测试库约束：本文件全部用 icpgate 前缀的唯一域名/行业，不与其他文件撞。
"""

from app.collectors.base import LeadDraft
from app.collectors.icp import compute_icp_status
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
    # cn_domestic：CN 证据但无出海信号
    assert compute_icp_status(is_cn=True) == "cn_domestic"
    # CN 证据兜底：国家码 CN / +86 号码
    assert compute_icp_status(country="CN") == "cn_domestic"
    assert compute_icp_status(phone_e164="+8613800138000") == "cn_domestic"
    # foreign：非 CN 且有评估结论（已富化 / meta_ads 来源做过中文判定）
    assert compute_icp_status(enriched_at=object()) == "foreign"
    assert (
        compute_icp_status(sources=[{"source": "meta_ads"}]) == "foreign"
    )
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

    # stats：四态分布存在且 foreign 计入
    r = await client.get("/api/v1/collect/stats", headers=h)
    icp_counts = r.json()["data"]["icp_counts"]
    assert set(icp_counts) == {"qualified", "cn_domestic", "foreign", "unknown"}
    assert icp_counts["foreign"] >= 1

    # 清理（共享测试库）
    await client.delete(f"/api/v1/collect/leads/{cn_id}", headers=h)
    await db_session.delete(foreign)
    await db_session.commit()
