"""CN 证据分级测试（2026-08-31 审计批次3：CJK 启发式误判通道可见化）。"""

import pytest


def test_cn_evidence_strong_paths():
    from app.collectors.icp import cn_evidence_of

    assert cn_evidence_of(is_cn=False, country="CN") == "strong"
    assert cn_evidence_of(is_cn=True, country="MY", phone_e164="+8613800138000") == "strong"
    # 中国招聘站来源 = 中国公司的一手证据
    assert cn_evidence_of(is_cn=True, country=None, sources=[{"source": "job_posting"}]) == "strong"
    # 人工录入/种子导入 = 显式人为断言
    assert cn_evidence_of(is_cn=True, sources=[{"source": "seed_import"}]) == "strong"


def test_cn_evidence_weak_when_only_cjk_heuristic():
    """仅 CJK 启发式（meta_ads 中文页名 / web_search 中文标题）→ weak。

    东南亚华人本地企业同样命中这些启发式，是 qualified 误判的主要入口。
    """
    from app.collectors.icp import cn_evidence_of

    assert cn_evidence_of(is_cn=True, country="MY", sources=[{"source": "meta_ads"}]) == "weak"
    assert cn_evidence_of(is_cn=True, sources=[{"source": "web_search"}]) == "weak"
    assert cn_evidence_of(is_cn=True, sources=None) == "weak"


def test_cn_evidence_empty_when_not_cn():
    from app.collectors.icp import cn_evidence_of

    assert cn_evidence_of(is_cn=False, country="MY") == ""
    assert cn_evidence_of(is_cn=False, country=None, phone_e164="+60111222333") == ""


def test_career_site_is_not_cn_strong_evidence():
    """官网招聘页巡检 ≠ 中国招聘站来源（§2.3 strong 四项口径）——
    对象含 Moka/北森/Workday 等任意 ATS，不构成 CN 硬证据，防止
    弱 CJK 东南亚企业被升格 strong 后回填 country=CN。"""
    from app.collectors.icp import cn_evidence_of

    assert cn_evidence_of(is_cn=True, country="MY", sources=[{"source": "career_site"}]) == "weak"


def test_mixed_sources_upgrade_weak_to_strong():
    """meta_ads 弱证据 + 后续 job_posting 合并进来 → 升级 strong。"""
    from app.collectors.icp import cn_evidence_of

    assert (
        cn_evidence_of(
            is_cn=True,
            sources=[{"source": "meta_ads"}, {"source": "job_posting"}],
        )
        == "strong"
    )


@pytest.mark.asyncio
async def test_detail_api_exposes_cn_evidence(client, admin_credentials):
    """详情 API 输出 cn_evidence。"""
    login = await client.post("/api/v1/auth/login", json=admin_credentials)
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    r = await client.post(
        "/api/v1/collect/leads",
        headers=headers,
        json={"name": "CN证据测试", "country": "CN", "website": "https://cn-ev-test.com"},
    )
    lead_id = r.json()["data"]["id"]
    detail = (await client.get(f"/api/v1/collect/leads/{lead_id}", headers=headers)).json()["data"]
    assert detail["cn_evidence"] == "strong"


@pytest.mark.asyncio
async def test_quality_overseas_queue_prioritizes_weak_evidence(client, admin_credentials):
    """质检 overseas 队列：弱 CN 证据的 qualified 优先出队（先量化误判率）。"""
    login = await client.post("/api/v1/auth/login", json=admin_credentials)
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    from app.db.session import async_session
    from app.models.lead import Lead

    async with async_session() as s:
        for name, country, sources in (
            # 强证据：CN 国家 + job_posting 来源
            ("强证据出海公司", "CN", [{"source": "job_posting"}]),
            # 弱证据：仅 meta_ads CJK 页名
            ("弱证据出海公司", "MY", [{"source": "meta_ads"}]),
        ):
            s.add(
                Lead(
                    name=name,
                    country=country,
                    is_cn=True,
                    icp_status="qualified",
                    overseas_signals={"markets": ["US"]},
                    sources=sources,
                    dedupe_key=f"namecity:q-{name}",
                )
            )
        await s.commit()

    r = await client.get(
        "/api/v1/quality/queue", headers=headers, params={"field": "overseas", "size": 2}
    )
    items = r.json()["data"]["items"]
    assert len(items) == 2
    assert items[0]["name"] == "弱证据出海公司"
    assert items[0]["evidence"]["cn_evidence"] == "weak"
    assert items[1]["evidence"]["cn_evidence"] == "strong"
