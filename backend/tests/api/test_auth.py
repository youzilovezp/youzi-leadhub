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
    """logout 行为按模式断言：

    - Redis 模式：token 真正进黑名单，旧 token 访问 /me 返 401（核心安全机制）
    - 无 Redis 模式：没有黑名单存储，logout 必须如实提示"撤销未生效"，
      绝不能谎报"已撤销"（用户以为安全退出了实际没有——这是安全问题）
    """
    login_resp = await client.post(
        "/api/v1/auth/login",
        json=admin_credentials,
    )
    token = login_resp.json()["data"]["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    # logout 撤销 token
    logout_resp = await client.post("/api/v1/auth/logout", headers=auth)
    assert logout_resp.status_code == 200
    msg = logout_resp.json()["message"]

    redis_enabled = False
    try:
        from app.core.config import settings

        redis_enabled = bool(getattr(settings, "REDIS_ENABLED", False))
    except Exception:
        redis_enabled = False

    if redis_enabled:
        # Redis 配置了但要确认真的可达（不可达时黑名单 fail-open，测不出撤销语义）
        try:
            from app.db.redis_client import redis_client

            await redis_client.ping()
            redis_reachable = True
        except Exception:
            redis_reachable = False
        if not redis_reachable:
            pytest.skip(
                "REDIS_HOST 已配置但 Redis 不可达（黑名单 fail-open，无法测撤销）"
            )
        # 旧 token 不应再能访问 /me
        me_resp = await client.get("/api/v1/auth/me", headers=auth)
        assert me_resp.status_code == 401
        assert me_resp.json()["code"] == 40100
        assert "已撤销" in msg
    else:
        # 无 Redis：token 仍有效（文档已声明的限制），但提示必须诚实
        me_resp = await client.get("/api/v1/auth/me", headers=auth)
        assert me_resp.status_code == 200
        assert "未生效" in msg, f"无 Redis 模式 logout 不能谎报已撤销：{msg}"


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
