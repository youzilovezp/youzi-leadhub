"""销售域全流程测试：分配 / 商机 CRM / 话术队列 / 预警 / 漏斗排行 / 需求类型 / NL 搜索。"""

from app.collectors.recommend import detect_need_types
from app.collectors.website_enrich import detect_whatsapp_numbers

# ---------- 单元：号码证据 + 需求类型 ----------


def test_lead_context_intent_signals_prompt():
    """LLM 上下文的意向信号拼接（v3）：零信号兜底文案，不产出悬挂分隔符。

    T2 审查 Important #1：零信号线索（手工录入未富化、sales_script 首触常见形态）
    此前产出退化行「等级：C（意向分 0；）」。
    """
    from types import SimpleNamespace

    from app.services.llm import _lead_context

    lead = SimpleNamespace(
        name="Zero Signal Co", industry=None, country="MY", city=None,
        grade="C", score=0, score_signals={},
        whatsapp_hit=False, whatsapp_url=None, fb_whatsapp=False, whatsapp_job=False,
        scenes=[], saas_signals={}, website=None, email=None, social={},
    )
    ctx = _lead_context(lead, [])
    assert "等级：C（意向分 0；暂未检测到意向信号）" in ctx
    # 有命中信号：中文标签 + 分值逐项拼接，兜底文案不出现
    lead.score = 55
    lead.score_signals = {"site_whatsapp": 25, "wa_ops_job": 30}
    ctx2 = _lead_context(lead, [])
    assert "等级：C（意向分 55；官网 WhatsApp 入口 25，在招 WhatsApp 运营岗 30）" in ctx2
    assert "暂未检测到意向信号" not in ctx2


def test_detect_whatsapp_numbers_dedup():
    html = """
    <a href="https://wa.me/60123456789">sales</a>
    <a href="https://wa.me/60987654321">support</a>
    <a href="https://wa.me/60123456789">sales again</a>
    <a href="https://api.whatsapp.com/send?phone=60555111222">marketing</a>
    """
    numbers = detect_whatsapp_numbers([html])
    assert numbers == ["60123456789", "60987654321", "60555111222"]


def test_detect_need_types_all_five():
    """六类需求（§4.4 A-E + F 广告线）：消息/API升级/客服/营销/私域/广告的信号组合。"""
    needs = detect_need_types(
        whatsapp_hit=True,
        whatsapp_url="https://wa.me/60",
        whatsapp_numbers=["1", "2"],
        whatsapp_job=True,
        scenes=["transactional", "marketing", "customer_service"],
        saas_signals={},
        sources=[{"source": "meta_ads"}],
    )
    types = {n["type"] for n in needs}
    assert types == {
        "messaging", "api_upgrade", "customer_service", "marketing", "private_domain", "ads",
    }
    # 每类都带中文标签与卖点
    assert all(n["label"] and n["selling"] for n in needs)


def test_detect_need_types_empty_without_whatsapp():
    """无 WhatsApp 使用 → 五类 WA 线需求都不判；广告线（F）与 WA 无关，在投即成立。"""
    needs = detect_need_types(
        whatsapp_hit=False,
        whatsapp_url=None,
        scenes=["marketing", "transactional"],
        sources=[{"source": "meta_ads"}],
    )
    assert {n["type"] for n in needs} == {"ads"}


def test_detect_need_types_api_upgrade_suppressed_by_saas():
    """已用 SaaS 工具（竞品）→ 不再判「API 升级」，替换商机走产品推荐。"""
    needs = detect_need_types(
        whatsapp_hit=True, whatsapp_url="https://wa.me/60", saas_signals={"crm": 1}
    )
    assert "api_upgrade" not in {n["type"] for n in needs}


# ---------- API 集成 ----------


async def _login(client, admin_credentials):
    r = await client.post("/api/v1/auth/login", json=admin_credentials)
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


async def _mk_lead(client, headers, name: str, **extra) -> dict:
    r = await client.post("/api/v1/collect/leads", headers=headers, json={"name": name, **extra})
    return r.json()["data"]


async def test_assign_release_and_auto_assign(client, admin_credentials):
    """分配/释放（§24/§44）+ 自动分配轮转。"""
    h = await _login(client, admin_credentials)
    # 两个销售账号
    for i in (1, 2):
        await client.post(
            "/api/v1/users",
            headers=h,
            json={"username": f"assignee{i}", "password": "pass-123456", "nickname": f"销售{i}"},
        )
    lead = await _mk_lead(client, h, "Assign Co", country="MY", website="https://assignco.com", industry="assign_test")

    # 手动分配
    r = await client.post(f"/api/v1/collect/leads/{lead['id']}/assign", headers=h, json={"owner_id": 2})
    assert r.json()["data"]["owner_id"] == 2
    assert r.json()["data"]["follow_status"] == "pending"  # 认领后进入待跟进
    # 分配事件入时间线
    detail = (await client.get(f"/api/v1/collect/leads/{lead['id']}", headers=h)).json()["data"]
    assert any(e["event_type"] == "assigned" for e in detail["events"])

    # 释放回共享池
    r = await client.post(f"/api/v1/collect/leads/{lead['id']}/release", headers=h)
    assert r.json()["data"]["owner_id"] is None
    assert r.json()["data"]["follow_status"] == "unassigned"

    # 自动分配：两条线索轮转给两个销售
    lead2 = await _mk_lead(client, h, "Assign Co 2", country="MY", website="https://assign2.com", industry="assign_test")
    r = await client.post(
        "/api/v1/collect/leads/auto-assign",
        headers=h,
        json={"owner_ids": [2, 3], "max_per_owner": 10, "limit": 10, "industry": "assign_test"},  # 共享测试库，用唯一行业圈定范围
    )
    data = r.json()["data"]
    assert data["assigned_count"] == 2
    per = {x["owner_id"]: x["count"] for x in data["per_owner"]}
    assert per == {2: 1, 3: 1}  # 轮转均衡

    # 清理
    for lid in (lead["id"], lead2["id"]):
        await client.delete(f"/api/v1/collect/leads/{lid}", headers=h)


async def test_auto_assign_skips_non_buyer(client, admin_credentials):
    """终审修复波：auto-assign 共享池同样过 ICP 门——non_buyer（媒体/社区）不被轮转给销售。"""
    h = await _login(client, admin_credentials)
    r = await client.post(
        "/api/v1/users",
        headers=h,
        json={"username": "fixwave_sales", "password": "pass-123456", "nickname": "修复波销售"},
    )
    owner_id = r.json()["data"]["id"]
    # 正常共享池线索（unknown 不做有罪推定，可分配）+ 名称命中买家黑名单的线索
    normal = await _mk_lead(
        client, h, "Fixwave Normal Co", country="MY",
        website="https://fixwave-normal.com", industry="fixwave_test",
    )
    non_buyer = await _mk_lead(
        client, h, "Fixwave 跨境卖家社区", country="CN",
        website="https://fixwave-community.com", industry="fixwave_test",
    )
    assert non_buyer["icp_status"] == "non_buyer"  # 前置：确实命中买家门

    r = await client.post(
        "/api/v1/collect/leads/auto-assign",
        headers=h,
        json={"owner_ids": [owner_id], "max_per_owner": 10, "limit": 10, "industry": "fixwave_test"},
    )
    data = r.json()["data"]
    assert data["assigned_count"] == 1
    # 库内核验：正常线索被分配，non_buyer 留在共享池
    got_normal = (await client.get(f"/api/v1/collect/leads/{normal['id']}", headers=h)).json()["data"]
    got_non_buyer = (await client.get(f"/api/v1/collect/leads/{non_buyer['id']}", headers=h)).json()["data"]
    assert got_normal["owner_id"] == owner_id
    assert got_non_buyer["owner_id"] is None

    for lid in (normal["id"], non_buyer["id"]):
        await client.delete(f"/api/v1/collect/leads/{lid}", headers=h)


async def test_alerts_endpoint(client, admin_credentials):
    """预警中心（§55）：is_alert 事件可查（无预警时为空也成立）。"""
    h = await _login(client, admin_credentials)
    r = await client.get("/api/v1/sales/alerts", headers=h)
    assert r.status_code == 200
    body = r.json()["data"]
    assert "items" in body and "total" in body


async def test_ai_analysis_fallback(client, admin_credentials):
    """AI 分析（§25）未配置 LLM → 规则模板降级。"""
    h = await _login(client, admin_credentials)
    lead = await _mk_lead(client, h, "AI Co", country="MY", website="https://aico.com")

    r = await client.get(f"/api/v1/sales/leads/{lead['id']}/ai-analysis", headers=h)
    data = r.json()["data"]
    assert data["generated_by"] in ("llm", "template")
    assert data["summary"] and data["entry_point"]

    await client.delete(f"/api/v1/collect/leads/{lead['id']}", headers=h)


async def test_data_sources_with_grade_dist(client, admin_credentials):
    """数据源管理（§33）：采集器清单 + 渠道 × 等级产出。"""
    h = await _login(client, admin_credentials)
    r = await client.get("/api/v1/sales/data-sources", headers=h)
    sources = r.json()["data"]
    names = {s["collector"] for s in sources}
    assert {"meta_ads", "website_enrich", "job_posting"} <= names
    assert all("grade_dist" in s and set(s["grade_dist"]) == {"S", "A", "B", "C"} for s in sources)


async def test_detail_includes_need_types_and_numbers(client, admin_credentials):
    """详情（§7 线索输出规格）：需求类型 + WhatsApp 号码证据链字段存在。"""
    h = await _login(client, admin_credentials)
    lead = await _mk_lead(client, h, "Spec Co", country="MY", website="https://specco.com")
    detail = (await client.get(f"/api/v1/collect/leads/{lead['id']}", headers=h)).json()["data"]
    assert "need_types" in detail
    assert "whatsapp_numbers" in detail
    assert "target_countries" in detail
    await client.delete(f"/api/v1/collect/leads/{lead['id']}", headers=h)
