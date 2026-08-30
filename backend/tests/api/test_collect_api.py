"""采集 API 层核心流程：录入合并、隐式富化任务、筛选、统计、跟进、权限。"""


async def _login(client, admin_credentials):
    r = await client.post("/api/v1/auth/login", json=admin_credentials)
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


async def _login_sales(client, admin_credentials, username="sales01"):
    """建一个普通销售账号并登录（is_superuser=False）。"""
    h = await _login(client, admin_credentials)
    await client.post("/api/v1/users", headers=h, json={
        "username": username, "password": "sales-pass-123", "nickname": "销售一号"})
    r = await client.post("/api/v1/auth/login",
                          json={"username": username, "password": "sales-pass-123"})
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


async def test_follow_up_flow(client, admin_credentials):
    """跟进：更新线索状态/跟进人 + 写历史 + 筛选（含「待跟进含 NULL」「该回访」）。"""
    h = await _login(client, admin_credentials)
    r = await client.post("/api/v1/collect/leads", headers=h, json={
        "name": "Follow Co", "country": "MY", "website": "https://followco.com"})
    lead = r.json()["data"]

    # 首次跟进：缺省跟进人 = 当前用户；状态 + 时间落库
    r = await client.post(f"/api/v1/collect/leads/{lead['id']}/follow-up", headers=h,
                          json={"status": "interested", "note": "已加 WhatsApp，聊得不错"})
    updated = r.json()["data"]
    assert updated["follow_status"] == "interested"
    assert updated["owner_id"] is not None and updated["owner_name"]
    assert updated["last_followed_at"] is not None

    # 历史多一条，字段齐全
    records = (await client.get(f"/api/v1/collect/leads/{lead['id']}/follow-ups", headers=h)).json()["data"]
    assert len(records) == 1
    assert records[0]["status"] == "interested" and records[0]["note"] == "已加 WhatsApp，聊得不错"
    assert records[0]["user_name"] == updated["owner_name"]

    # 列表筛选：跟进状态命中；owner_name 注入
    r = await client.get("/api/v1/collect/leads", headers=h,
                         params={"follow_status": "interested", "keyword": "followco"})
    items = r.json()["data"]["items"]
    assert any(i["id"] == lead["id"] and i["owner_name"] for i in items)

    # 「待跟进」筛选应包含从未跟进（follow_status=NULL）的线索
    r = await client.post("/api/v1/collect/leads", headers=h, json={
        "name": "Pending Co", "country": "MY", "website": "https://pendingco.com"})
    fresh = r.json()["data"]
    r = await client.get("/api/v1/collect/leads", headers=h, params={"follow_status": "pending"})
    ids = [i["id"] for i in r.json()["data"]["items"]]
    assert fresh["id"] in ids and lead["id"] not in ids

    # 该回访：下次跟进时间设到过去 → due_follow 命中
    r = await client.post(f"/api/v1/collect/leads/{lead['id']}/follow-up", headers=h,
                          json={"status": "following", "next_follow_at": "2020-01-01T00:00:00Z"})
    assert r.json()["code"] == 0
    r = await client.get("/api/v1/collect/leads", headers=h, params={"due_follow": True})
    assert any(i["id"] == lead["id"] for i in r.json()["data"]["items"])
    stats = (await client.get("/api/v1/collect/stats", headers=h)).json()["data"]
    assert stats["pending_leads"] >= 1 and stats["due_follow_leads"] >= 1

    # 非法状态 422；不存在的线索 404；指派不存在跟进人报业务错
    r = await client.post(f"/api/v1/collect/leads/{lead['id']}/follow-up", headers=h,
                          json={"status": "whatever"})
    assert r.status_code == 422
    r = await client.post("/api/v1/collect/leads/999999/follow-up", headers=h,
                          json={"status": "following"})
    assert r.status_code == 404
    r = await client.post(f"/api/v1/collect/leads/{lead['id']}/follow-up", headers=h,
                          json={"status": "following", "owner_id": 999999})
    assert r.status_code == 400

    # 清理
    for lid in (lead["id"], fresh["id"]):
        await client.delete(f"/api/v1/collect/leads/{lid}", headers=h)


async def test_sales_permissions(client, admin_credentials):
    """权限矩阵：销售能看线索/任务、能跟进；任务管控与删线索仍仅管理员。"""
    sales = await _login_sales(client, admin_credentials)
    admin = await _login(client, admin_credentials)

    r = await client.post("/api/v1/collect/leads", headers=admin, json={
        "name": "Perm Co", "country": "MY", "website": "https://permco.com"})
    lead = r.json()["data"]

    # 销售：线索列表/统计/任务列表 可读，跟进可写
    assert (await client.get("/api/v1/collect/leads", headers=sales)).status_code == 200
    assert (await client.get("/api/v1/collect/stats", headers=sales)).status_code == 200
    assert (await client.get("/api/v1/collect/tasks", headers=sales)).status_code == 200
    r = await client.post(f"/api/v1/collect/leads/{lead['id']}/follow-up", headers=sales,
                          json={"status": "following", "note": "销售第一次联系"})
    assert r.json()["data"]["owner_name"] == "销售一号"

    # 销售：任务管控 + 删线索 → 403
    r = await client.post("/api/v1/collect/tasks", headers=sales,
                          json={"collector": "website_enrich", "params": {}})
    assert r.status_code == 403
    assert (await client.delete(f"/api/v1/collect/leads/{lead['id']}", headers=sales)).status_code == 403

    # 管理员建任务带操作人；列表回显 created_by_name
    r = await client.post("/api/v1/collect/tasks", headers=admin,
                          json={"collector": "website_enrich", "params": {}})
    task = r.json()["data"]
    items = (await client.get("/api/v1/collect/tasks", headers=admin)).json()["data"]["items"]
    mine = next(t for t in items if t["id"] == task["id"])
    assert mine["created_by"] is not None and mine["created_by_name"]

    # 清理（销售不能删，用管理员）
    await client.delete(f"/api/v1/collect/tasks/{task['id']}", headers=admin)
    await client.delete(f"/api/v1/collect/leads/{lead['id']}", headers=admin)
