"""销售域全流程测试：分配 / 商机 CRM / 话术队列 / 预警 / 漏斗排行 / 需求类型 / NL 搜索。"""

from app.collectors.recommend import detect_need_types
from app.collectors.website_enrich import detect_whatsapp_numbers

# ---------- 单元：号码证据 + 需求类型 ----------


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
    """五类需求（§4.4）：消息/API升级/客服/营销/私域的判断信号组合。"""
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
    assert types == {"messaging", "api_upgrade", "customer_service", "marketing", "private_domain"}
    # 每类都带中文标签与卖点
    assert all(n["label"] and n["selling"] for n in needs)


def test_detect_need_types_empty_without_whatsapp():
    assert (
        detect_need_types(
            whatsapp_hit=False,
            whatsapp_url=None,
            scenes=["marketing", "transactional"],
            sources=[{"source": "meta_ads"}],
        )
        == []
    )


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


async def test_opportunity_lifecycle_and_funnel(client, admin_credentials):
    """商机（§37）：创建 → 推进 → 成交联动线索状态 → 漏斗/排行统计。"""
    h = await _login(client, admin_credentials)
    lead = await _mk_lead(client, h, "Opp Co", country="MY", website="https://oppco.com")

    r = await client.post(
        f"/api/v1/sales/leads/{lead['id']}/opportunities",
        headers=h,
        json={"name": "WA 客服 SaaS 年单", "amount": 12000},
    )
    opp = r.json()["data"]
    assert opp["stage"] == "opportunity" and opp["amount"] == 12000

    # 创建商机联动线索状态
    detail = (await client.get(f"/api/v1/collect/leads/{lead['id']}", headers=h)).json()["data"]
    assert detail["follow_status"] == "opportunity"
    assert len(detail["opportunities"]) == 1

    # 非法阶段被拦
    r = await client.put(
        f"/api/v1/sales/leads/{lead['id']}/opportunities/{opp['id']}",
        headers=h,
        json={"stage": "whatever"},
    )
    assert r.json()["code"] == 40001

    # 推进到成交：won_at 落库 + 线索状态联动
    r = await client.put(
        f"/api/v1/sales/leads/{lead['id']}/opportunities/{opp['id']}",
        headers=h,
        json={"stage": "won"},
    )
    assert r.json()["data"]["won_at"] is not None
    detail = (await client.get(f"/api/v1/collect/leads/{lead['id']}", headers=h)).json()["data"]
    assert detail["follow_status"] == "won"

    # 漏斗（§38）与排行榜（§40）
    funnel = (await client.get("/api/v1/sales/funnel", headers=h)).json()["data"]
    assert funnel["stages"].get("won", 0) >= 1
    assert funnel["won_amount"] >= 12000
    board = (await client.get("/api/v1/sales/leaderboard", headers=h)).json()["data"]
    assert any(row["won"] >= 1 and row["won_amount"] >= 12000 for row in board)

    await client.delete(f"/api/v1/collect/leads/{lead['id']}", headers=h)


async def test_message_queue_flow(client, admin_credentials):
    """话术队列（§56）：生成（未配置 LLM → 模板降级）→ 通过 → 标记已发。"""
    h = await _login(client, admin_credentials)
    lead = await _mk_lead(client, h, "Msg Co", country="MY", website="https://msgco.com")

    r = await client.post(f"/api/v1/sales/leads/{lead['id']}/messages/generate", headers=h)
    msg = r.json()["data"]
    assert msg["status"] == "draft" and msg["generated_by"] in ("llm", "template")
    assert len(msg["content"]) > 30

    # 未审核不能直接标记已发
    r = await client.post(f"/api/v1/sales/messages/{msg['id']}/review", headers=h, json={"action": "mark_sent"})
    assert r.json()["code"] == 40001
    # 通过 → 标记已发
    r = await client.post(f"/api/v1/sales/messages/{msg['id']}/review", headers=h, json={"action": "approve"})
    assert r.json()["data"]["status"] == "approved"
    r = await client.post(f"/api/v1/sales/messages/{msg['id']}/review", headers=h, json={"action": "mark_sent"})
    assert r.json()["data"]["status"] == "sent" and r.json()["data"]["sent_at"]

    # 队列筛选
    msgs = (await client.get("/api/v1/sales/messages", headers=h, params={"status": "sent"})).json()["data"]
    assert any(m["id"] == msg["id"] for m in msgs["items"])

    await client.delete(f"/api/v1/collect/leads/{lead['id']}", headers=h)


async def test_alerts_endpoint(client, admin_credentials):
    """预警中心（§55）：is_alert 事件可查（无预警时为空也成立）。"""
    h = await _login(client, admin_credentials)
    r = await client.get("/api/v1/sales/alerts", headers=h)
    assert r.status_code == 200
    body = r.json()["data"]
    assert "items" in body and "total" in body


async def test_ai_analysis_fallback_and_nl_requires_llm(client, admin_credentials):
    """AI 分析（§25）未配置 LLM → 规则模板降级；NL 搜索（§27）未配置 → 明确业务错误。"""
    h = await _login(client, admin_credentials)
    lead = await _mk_lead(client, h, "AI Co", country="MY", website="https://aico.com")

    r = await client.get(f"/api/v1/sales/leads/{lead['id']}/ai-analysis", headers=h)
    data = r.json()["data"]
    assert data["generated_by"] in ("llm", "template")
    assert data["summary"] and data["entry_point"]

    r = await client.post(
        "/api/v1/sales/leads/search-nl", headers=h, json={"text": "深圳跨境电商美国市场"}
    )
    # 未配置 LLM 的环境 → 40001 业务错误（配置了则返回 params）
    assert r.json()["code"] in (0, 40001)
    if r.json()["code"] == 0:
        assert "params" in r.json()["data"]

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
