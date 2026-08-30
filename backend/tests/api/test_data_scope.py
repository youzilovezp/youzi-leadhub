"""数据权限（PRD §43）接线回归测试。

own 级用户只能访问自己的线索 + 共享池；越权访问一律 404（不泄露存在性）。
follow-up 不能越权改派 owner（撞单锁定语义做实）。
stats:read 权限码控制漏斗/排行/数据源。
"""

import pytest
from httpx import AsyncClient


async def _make_scoped_sales(
    client: AsyncClient, admin_headers: dict, username: str, scope: str = "own"
) -> dict:
    """建一个指定 data_scope 的销售账号并登录，返回 headers。"""
    r = await client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"username": username, "password": "Scope-Pass-123", "nickname": username},
    )
    assert r.status_code == 200, r.text

    from sqlalchemy import update as sa_update

    from app.db.session import async_session
    from app.models.user import User

    async with async_session() as s:
        await s.execute(
            sa_update(User).where(User.username == username).values(data_scope=scope)
        )
        await s.commit()

    login = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": "Scope-Pass-123"}
    )
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


async def _create_manual_lead(client: AsyncClient, headers: dict, name: str) -> dict:
    r = await client.post(
        "/api/v1/collect/leads",
        headers=headers,
        json={"name": name, "country": "MY", "website": f"https://{name.lower()}.com"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


@pytest.mark.asyncio
async def test_own_scope_isolation(client: AsyncClient, admin_credentials):
    """own 级销售互相看不见对方的线索（详情/联系人/跟进/事件全部 404）。"""
    admin = {"Authorization": f"Bearer {(await client.post('/api/v1/auth/login', json=admin_credentials)).json()['data']['access_token']}"}
    a = await _make_scoped_sales(client, admin, "scope-a")
    b = await _make_scoped_sales(client, admin, "scope-b")

    # a 认领一条线索（跟进即持有）
    lead_a = await _create_manual_lead(client, admin, "ScopeA Co")
    r = await client.post(
        f"/api/v1/collect/leads/{lead_a['id']}/follow-up",
        headers=a,
        json={"status": "contacted", "note": "a 认领"},
    )
    assert r.status_code == 200

    # b（own 级）访问 a 的线索：详情/联系人/跟进写入/事件 → 全部 404
    assert (await client.get(f"/api/v1/collect/leads/{lead_a['id']}", headers=b)).status_code == 404
    assert (await client.get(f"/api/v1/collect/leads/{lead_a['id']}/contacts", headers=b)).status_code == 404
    assert (
        await client.post(
            f"/api/v1/collect/leads/{lead_a['id']}/follow-up",
            headers=b,
            json={"status": "contacted"},
        )
    ).status_code == 404
    assert (await client.get(f"/api/v1/collect/leads/{lead_a['id']}/events", headers=b)).status_code == 404
    # 商机/AI 同样拒绝
    assert (
        await client.post(f"/api/v1/sales/leads/{lead_a['id']}/opportunities", headers=b, json={"name": "x"})
    ).status_code == 404
    assert (await client.get(f"/api/v1/sales/leads/{lead_a['id']}/ai-analysis", headers=b)).status_code == 404

    # b 的列表里没有 a 的线索；a 的列表里有
    b_list = (await client.get("/api/v1/collect/leads", headers=b)).json()["data"]["items"]
    assert all(item["id"] != lead_a["id"] for item in b_list)
    a_list = (await client.get("/api/v1/collect/leads", headers=a)).json()["data"]["items"]
    assert any(item["id"] == lead_a["id"] for item in a_list)

    # 共享池（无 owner）b 可见可跟进
    shared = await _create_manual_lead(client, admin, "ScopeShared Co")
    b_list2 = (await client.get("/api/v1/collect/leads", headers=b)).json()["data"]["items"]
    assert any(item["id"] == shared["id"] for item in b_list2)
    r = await client.post(
        f"/api/v1/collect/leads/{shared['id']}/follow-up",
        headers=b,
        json={"status": "pending", "note": "b 认领共享池"},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_followup_cannot_reassign_without_perm(client: AsyncClient, admin_credentials):
    """普通销售不能用 follow-up 把线索 owner 改派给他人（需 assign:lead）。"""
    admin = {"Authorization": f"Bearer {(await client.post('/api/v1/auth/login', json=admin_credentials)).json()['data']['access_token']}"}
    a = await _make_scoped_sales(client, admin, "scope-c")
    b = await _make_scoped_sales(client, admin, "scope-d")

    lead = await _create_manual_lead(client, admin, "ScopeAssign Co")
    # a 先认领
    await client.post(
        f"/api/v1/collect/leads/{lead['id']}/follow-up", headers=a, json={"status": "contacted"}
    )
    # a（无 assign:lead）试图把 owner 改成 b 的 user id → 403
    users = (await client.get("/api/v1/users", headers=admin)).json()["data"]
    # users 可能是分页结构或列表
    user_list = users["items"] if isinstance(users, dict) and "items" in users else users
    b_id = next(u["id"] for u in user_list if u["username"] == "scope-d")
    r = await client.post(
        f"/api/v1/collect/leads/{lead['id']}/follow-up",
        headers=a,
        json={"status": "contacted", "owner_id": b_id},
    )
    assert r.status_code == 403

    # 主管（admin，超管旁路）可以指派
    r = await client.post(
        f"/api/v1/collect/leads/{lead['id']}/follow-up",
        headers=admin,
        json={"status": "contacted", "owner_id": b_id},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_stats_endpoints_require_permission(client: AsyncClient, admin_credentials):
    """漏斗/排行/数据源需要 stats:read 权限码；无角色用户 403，超管 200。"""
    admin = {"Authorization": f"Bearer {(await client.post('/api/v1/auth/login', json=admin_credentials)).json()['data']['access_token']}"}
    # 无角色用户（权限码为空集）
    r = await client.post(
        "/api/v1/users", headers=admin,
        json={"username": "no-role-user", "password": "NoRole-Pass-1", "nickname": "无角色"},
    )
    assert r.status_code == 200
    login = await client.post(
        "/api/v1/auth/login", json={"username": "no-role-user", "password": "NoRole-Pass-1"}
    )
    no_role = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    for path in ("/api/v1/sales/funnel", "/api/v1/sales/leaderboard", "/api/v1/sales/data-sources"):
        assert (await client.get(path, headers=no_role)).status_code == 403, path
        assert (await client.get(path, headers=admin)).status_code == 200, path
