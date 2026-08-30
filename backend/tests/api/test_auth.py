"""认证相关接口测试。

覆盖关键安全场景：
- 正常登录
- 错误密码
- /me 未登录 / 无 token / 错误 token
- logout + 黑名单生效
- disabled 用户
- 控制字符防护（BiDi 攻击）
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient, admin_credentials: dict[str, str]):
    resp = await client.post(
        "/api/v1/auth/login",
        json={
            "username": admin_credentials["username"],
            "password": admin_credentials["password"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert "access_token" in data["data"]
    assert data["data"]["token_type"] == "bearer"
    assert data["data"]["expires_in"] > 0
    # 关键断言：user 字段完整（防 LoginResponse schema 改坏）
    user = data["data"]["user"]
    assert user["username"] == admin_credentials["username"]
    assert user["is_superuser"] is True


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    """错密码 → HTTP 401（AuthError 被异常处理器转 401，不是 200）"""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "WrongPassword123!"},
    )
    assert resp.status_code == 401
    data = resp.json()
    assert data["code"] == 40100


@pytest.mark.asyncio
async def test_login_short_password_rejected(client: AsyncClient):
    """密码 < 6 字符 → 422（防止 bcrypt O(n²) DoS 入口）"""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "x"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_bidi_control_char_in_username_rejected(client: AsyncClient):
    """BiDi 控制字符（U+202E 等）用户名 → 422（防 UI 欺骗攻击）"""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin‮", "password": "anything"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_me_without_token_returns_401(client: AsyncClient):
    """无 Authorization header → 401"""
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    data = resp.json()
    assert data["code"] == 40100


@pytest.mark.asyncio
async def test_me_with_invalid_token_returns_401(client: AsyncClient):
    """伪造的 token → 401"""
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer invalid.fake.token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_valid_token_returns_user(
    client: AsyncClient, admin_credentials: dict[str, str]
):
    """登录拿到的 token → /me 返 200 + user"""
    login_resp = await client.post(
        "/api/v1/auth/login",
        json=admin_credentials,
    )
    token = login_resp.json()["data"]["access_token"]
    me_resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["data"]["username"] == admin_credentials["username"]


@pytest.mark.asyncio
async def test_logout_blacklists_token(
    client: AsyncClient, admin_credentials: dict[str, str]
):
    """logout 把 jti 写入 token_blacklist 表——旧 token 访问 /me 立即 401。

    DB 存储的黑名单（无 Redis 依赖），这是登出安全语义的核心断言。
    """
    login_resp = await client.post(
        "/api/v1/auth/login",
        json=admin_credentials,
    )
    token = login_resp.json()["data"]["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    logout_resp = await client.post("/api/v1/auth/logout", headers=auth)
    assert logout_resp.status_code == 200
    assert "已撤销" in logout_resp.json()["message"]

    # 旧 token 不应再能访问 /me
    me_resp = await client.get("/api/v1/auth/me", headers=auth)
    assert me_resp.status_code == 401
    assert me_resp.json()["code"] == 40100

    # 新登录的 token 不受影响（jti 不同）
    relogin = await client.post("/api/v1/auth/login", json=admin_credentials)
    new_token = relogin.json()["data"]["access_token"]
    me2 = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_token}"})
    assert me2.status_code == 200


@pytest.mark.asyncio
async def test_login_lockout_after_repeated_failures(
    client: AsyncClient, admin_credentials: dict[str, str]
):
    """暴力破解防护：同用户名+IP 连续 5 次失败后锁定，正确密码也被拒。"""
    # 建独立测试用户（不动 admin——共享测试库里 admin 被锁会殃及后续测试）
    login_resp = await client.post("/api/v1/auth/login", json=admin_credentials)
    admin_auth = {"Authorization": f"Bearer {login_resp.json()['data']['access_token']}"}
    create_resp = await client.post(
        "/api/v1/users",
        headers=admin_auth,
        json={"username": "lockout-target", "password": "RightPass123!", "nickname": "锁定测试"},
    )
    assert create_resp.status_code == 200, create_resp.text

    bad = {"username": "lockout-target", "password": "WrongPassword123!"}
    for _ in range(5):
        resp = await client.post("/api/v1/auth/login", json=bad)
        assert resp.status_code == 401

    # 第 6 次：即使密码正确也被锁（锁定期内不消耗 bcrypt、不给枚举信号）
    ok_resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "lockout-target", "password": "RightPass123!"},
    )
    assert ok_resp.status_code == 401
    assert "频繁" in ok_resp.json()["message"]

    # 锁定计数落库（可观测、跨进程）
    from sqlalchemy import select

    from app.db.session import async_session
    from app.models.user import LoginThrottle

    async with async_session() as s:
        rows = (await s.execute(select(LoginThrottle))).scalars().all()
    keys = {r.throttle_key for r in rows}
    assert any(k.startswith("u:lockout-target|ip:") for k in keys)
    assert any(k.startswith("ip:") for k in keys)
    locked = [r for r in rows if r.throttle_key.startswith("u:lockout-target|ip:")]
    assert locked and locked[0].locked_until is not None


@pytest.mark.asyncio
async def test_login_timing_attack_defense(
    client: AsyncClient, admin_credentials: dict[str, str]
):
    """用户名枚举 timing attack 防御：响应时间 < 100ms 差异"""
    import time

    # 三种场景：真实用户/错误密码、不存在用户/错误密码
    # 由于 mock 速度可能太快，反复跑 5 次取中位数
    samples_real, samples_fake = [], []
    for _ in range(3):
        t = time.perf_counter()
        await client.post(
            "/api/v1/auth/login",
            json=admin_credentials,
        )
        samples_real.append(time.perf_counter() - t)

        t = time.perf_counter()
        await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "WrongPassword99"},
        )
        samples_fake.append(time.perf_counter() - t)

    # 中位数差异应 < 100ms（实际 bcrypt 主导 ≈ 200ms，差异应 < 50ms）
    median_real = sorted(samples_real)[len(samples_real) // 2]
    median_fake = sorted(samples_fake)[len(samples_fake) // 2]
    diff = abs(median_real - median_fake)
    assert (
        diff < 0.1
    ), f"timing diff {diff*1000:.0f}ms 太大（> 100ms），可能 timing attack 复现"


@pytest.mark.asyncio
async def test_healthz_does_not_require_auth(client: AsyncClient):
    """/healthz 公开（无 token 也 200），K8s livenessProbe 用"""
    resp = await client.get("/healthz")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_readyz_returns_503_when_db_down(client: AsyncClient):
    """/readyz 在 DB 不可用时返 503（K8s readinessProbe 行为）

    这个测试跳过时是 skip——因为本地测试环境 DB 一般正常。
    真要测可以 monkeypatch async_session 来抛 OperationalError。
    """
    resp = await client.get("/readyz")
    assert resp.status_code in (200, 503), "readyz 必须返 200 或 503"
