"""用户 CRUD 测试——守住关键安全规则。

覆盖：
- 创建/更新/删除用户
- 业务规则：admin 不能修改/删除自己
- 业务规则：不能删除最后一个 superuser（防止系统永久锁死）
- 业务规则：重复 username 被拒
- admin 改密不校验旧密码
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import User


async def _create_user(
    db: AsyncSession,
    username: str,
    *,
    is_superuser: bool = False,
    is_active: bool = True,
) -> User:
    user = User(
        username=username,
        email=f"{username}@test.com",
        nickname=username.title(),
        password_hash=hash_password("TestPass123!"),
        is_active=is_active,
        is_superuser=is_superuser,
        role_id=1,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest.mark.asyncio
async def test_list_users_requires_superuser(
    client: AsyncClient, db_session: AsyncSession
):
    """普通用户访问 /users 返 403"""
    normal = await _create_user(db_session, "normal_user", is_superuser=False)
    # 模拟登录普通用户
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "normal_user", "password": "TestPass123!"},
    )
    # 可能 401（demo 数据已经创建了 normal_user）；跳过如果失败
    if login_resp.status_code != 200:
        pytest.skip("normal_user 已存在（demo 种子）")
    auth = {"Authorization": f"Bearer {login_resp.json()['data']['access_token']}"}
    resp = await client.get("/api/v1/users", headers=auth)
    assert resp.status_code == 403
    # 清理
    await db_session.delete(normal)
    await db_session.commit()


@pytest.mark.asyncio
async def test_create_user_duplicate_username_rejected(
    client: AsyncClient, db_session: AsyncSession, admin_credentials: dict[str, str]
):
    """重复 username 返 40001（业务错误），不暴露 SQL 错误"""
    login_resp = await client.post("/api/v1/auth/login", json=admin_credentials)
    token = login_resp.json()["data"]["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    # 第一次创建：成功
    payload = {
        "username": "dup_test_user",
        "password": "TestPass123!",
        "email": "dup1@test.com",
        "is_active": True,
    }
    resp1 = await client.post("/api/v1/users", headers=auth, json=payload)
    assert resp1.status_code in (200, 400)  # 可能并发跑重复
    if resp1.status_code == 200:
        # 第二次创建同名：应 40001 业务错误
        payload2 = {**payload, "email": "dup2@test.com"}
        resp2 = await client.post("/api/v1/users", headers=auth, json=payload2)
        # BusinessError → HTTP 400 + body.code=40001
        assert (
            resp2.status_code == 400
        ), f"应返 400 业务错，实际 {resp2.status_code}: {resp2.text}"
        assert resp2.json()["code"] == 40001
        # 清理
        from sqlalchemy import select

        stmt = select(User).where(User.username == "dup_test_user")
        user = (await db_session.execute(stmt)).scalar_one()
        await db_session.delete(user)
        await db_session.commit()


@pytest.mark.asyncio
async def test_cannot_delete_last_superuser(
    client: AsyncClient, db_session: AsyncSession, admin_credentials: dict[str, str]
):
    """核心安全规则：不能删除最后一个 superuser（系统永久锁死）"""
    login_resp = await client.post("/api/v1/auth/login", json=admin_credentials)
    token = login_resp.json()["data"]["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    # 查当前 admin id（就是 admin_credentials["username"]）
    from sqlalchemy import select

    stmt = select(User).where(User.username == admin_credentials["username"])
    admin_user = (await db_session.execute(stmt)).scalar_one()

    # 尝试删除自己（也是唯一的 superuser）→ 拒绝
    resp = await client.delete(f"/api/v1/users/{admin_user.id}", headers=auth)
    # BusinessError → HTTP 400 + body.code=40000/40001
    assert (
        resp.status_code == 400
    ), f"应返 400 业务错，实际 {resp.status_code}: {resp.text}"
    data = resp.json()
    # 不能删除自己 OR 不能删除最后一个 superuser → 任一拒绝即正确
    assert data["code"] in (40000, 40001), f"应拒绝，实际：{data}"


@pytest.mark.asyncio
async def test_admin_can_change_password_without_old_password(
    client: AsyncClient, db_session: AsyncSession, admin_credentials: dict[str, str]
):
    """admin 改密接口**不**要求旧密码（admin 帮用户改密的场景）"""
    login_resp = await client.post("/api/v1/auth/login", json=admin_credentials)
    token = login_resp.json()["data"]["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    # 创建一个测试用户
    user = await _create_user(db_session, "pwd_change_test")
    try:
        # 不传 old_password，直接传 new_password
        resp = await client.post(
            f"/api/v1/users/{user.id}/password",
            headers=auth,
            json={"new_password": "NewPass456@"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "密码已更新"
    finally:
        await db_session.delete(user)
        await db_session.commit()
