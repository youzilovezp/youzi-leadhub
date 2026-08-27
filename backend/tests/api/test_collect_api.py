"""采集 API 层核心流程：录入合并、隐式富化任务、筛选、统计。"""


async def _login(client, admin_credentials):
    r = await client.post("/api/v1/auth/login", json=admin_credentials)
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


async def test_geo_options(client, admin_credentials):
    """国家/城市联动数据源：国家与城市映射一一对应（每个国家都有城市建议）。"""
    h = await _login(client, admin_credentials)
    data = (await client.get("/api/v1/collect/geo-options", headers=h)).json()["data"]
    codes = {c["value"] for c in data["countries"]}
    assert {"MY", "PH", "AE", "BR"} <= codes  # 目标市场核心国都在
    assert set(data["cities_by_country"]) <= codes  # 城市表不多余
    assert "Kuala Lumpur" in data["cities_by_country"]["MY"]


async def test_industries_chinese_labels(client, admin_credentials):
    """行业选项：value 保持原 token（筛选精确），label 出中文；未收录词表原样显示。"""
    h = await _login(client, admin_credentials)
    await client.post("/api/v1/collect/leads", headers=h, json={
        "name": "Zh Label Co", "country": "MY", "website": "https://zhlabel.com", "industry": "dentist"})
    await client.post("/api/v1/collect/leads", headers=h, json={
        "name": "Zh Custom Co", "country": "MY", "website": "https://zhcustom.com", "industry": "custom_xyz"})
    items = (await client.get("/api/v1/collect/industries", headers=h)).json()["data"]
    by_value = {i["value"]: i for i in items}
    assert by_value["dentist"]["label"] == "牙科诊所"
    assert by_value["dentist"]["count"] >= 1
    assert by_value["custom_xyz"]["label"] == "custom_xyz"  # 未收录原样，不瞎翻译
    # 清理
    for v in ("zhlabel.com", "zhcustom.com"):
        r = await client.get("/api/v1/collect/leads", headers=h, params={"keyword": v})
        for it in r.json()["data"]["items"]:
            await client.delete(f"/api/v1/collect/leads/{it['id']}", headers=h)


async def test_lead_create_merge_and_filters(client, admin_credentials):
    h = await _login(client, admin_credentials)
    # 录入同公司两次：第二次应合并（同 domain）
    r1 = await client.post("/api/v1/collect/leads", headers=h, json={
        "name": "Api Co", "country": "MY", "website": "https://apico.com"})
    r2 = await client.post("/api/v1/collect/leads", headers=h, json={
        "name": "Api Co", "country": "MY", "website": "http://www.apico.com",
        "email": "hi@apico.com"})
    assert r1.json()["data"]["id"] == r2.json()["data"]["id"]
    lead = r2.json()["data"]
    assert lead["email"] == "hi@apico.com"  # 合并补空
    assert len(lead["sources"]) == 1  # 同 source 不重复

    # 筛选：关键词 + min_score + has_website=false（P2 修复：false 不再被忽略）
    r = await client.get("/api/v1/collect/leads", headers=h,
                         params={"keyword": "apico", "min_score": 10})
    assert any(i["id"] == lead["id"] for i in r.json()["data"]["items"])
    r = await client.get("/api/v1/collect/leads", headers=h, params={"has_website": False})
    assert all(not i["website"] for i in r.json()["data"]["items"])

    # 勾选检测 → 隐式任务（runner 未启动，停留 queued）
    r = await client.post("/api/v1/collect/leads/check-whatsapp", headers=h,
                          json={"lead_ids": [lead["id"]]})
    task = r.json()["data"]
    assert task["is_implicit"] and task["collector"] == "website_enrich"
    assert task["params"]["lead_ids"] == [lead["id"]]
    assert task["status"] == "queued"

    # 统计口径
    stats = (await client.get("/api/v1/collect/stats", headers=h)).json()["data"]
    assert stats["total_leads"] >= 1 and stats["active_tasks"] >= 1

    # 删除
    r = await client.delete(f"/api/v1/collect/leads/{lead['id']}", headers=h)
    assert r.json()["code"] == 0
