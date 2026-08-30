"""
认证相关业务逻辑：登录（timing 防御 + 失败限流）。

暴力破解防护（DB 存储，跨进程有效，不依赖 Redis）：
- `u:<username>|ip:<ip>` 连续 5 次失败 → 锁定，指数退避 60s 起倍增封顶 1h
- `ip:<ip>` 纯来源 20 次失败 → 锁定（防同一 IP 撒网试多个用户名）
- 登录成功清零计数
"""

import asyncio
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AuthError
from app.core.security import create_access_token, hash_password, verify_password
from app.crud.user import user_crud
from app.schemas.user import LoginRequest, LoginResponse, UserOut

# 防御 timing attack：用户名不存在时仍跑一次假 bcrypt，让"用户不存在"和"密码错"
# 的响应时间差异降到最小（差异仅来自 SELECT 查询本身，几百微秒级）。
_DUMMY_HASH = hash_password("__timing_attack_defense__")

# 登录限流策略（详见模块 docstring）
_USER_FAIL_THRESHOLD = 5  # 同一 用户名+IP 连续失败锁
_IP_FAIL_THRESHOLD = 20  # 同一 IP（任意用户名）失败锁
_LOCK_BASE_SECONDS = 60  # 首次锁定时长，此后倍增
_LOCK_MAX_SECONDS = 3600  # 锁定上限 1 小时


def _client_ip(request_headers: Any | None, client_host: str | None) -> str:
    """取客户端 IP：反代场景优先 X-Forwarded-For 首跳，否则直连地址。"""
    fwd = request_headers.get("x-forwarded-for") if request_headers else None
    if fwd:
        first = str(fwd).split(",")[0].strip()
        if first:
            return first[:64]
    return (client_host or "unknown")[:64]


def _lock_seconds(fails: int, threshold: int) -> int:
    """超过阈值后的锁定时长：60s × 2^(超出次数-1)，封顶 1h。"""
    exceed = max(1, fails - threshold + 1)
    return min(_LOCK_BASE_SECONDS * 2 ** (exceed - 1), _LOCK_MAX_SECONDS)


async def _throttle_rows(keys: list[str]) -> dict[str, "tuple[int, datetime | None]"]:
    """读 throttle 行（独立短事务，读已提交数据）。"""
    from app.db.session import async_session
    from app.models.user import LoginThrottle

    async with async_session() as s:
        rows = (
            await s.execute(select(LoginThrottle).where(LoginThrottle.throttle_key.in_(keys)))
        ).scalars().all()
    return {r.throttle_key: (r.fail_count, r.locked_until) for r in rows}


async def _check_throttle(username: str, ip: str) -> None:
    """锁定中 → 直接拒绝（先于密码校验，锁定期内不消耗 bcrypt）。"""
    user_key = f"u:{username.lower()}|ip:{ip}"
    ip_key = f"ip:{ip}"
    rows = await _throttle_rows([user_key, ip_key])
    now = datetime.now(timezone.utc)
    for key, (fails, locked_until) in rows.items():
        if locked_until is not None:
            until = locked_until if locked_until.tzinfo else locked_until.replace(tzinfo=timezone.utc)
            if until > now:
                wait = math.ceil((until - now).total_seconds())
                logger.warning("auth.login.locked key={} fails={} wait={}s", key, fails, wait)
                raise AuthError(f"登录尝试过于频繁，请 {wait} 秒后再试")


async def _record_fail(username: str, ip: str) -> None:
    """失败计数 +1，达到阈值设锁定（独立事务提交，不随请求回滚丢失）。"""
    from app.db.session import async_session
    from app.models.user import LoginThrottle

    user_key = f"u:{username.lower()}|ip:{ip}"
    ip_key = f"ip:{ip}"
    now = datetime.now(timezone.utc)
    async with async_session() as s:
        for key, threshold in ((user_key, _USER_FAIL_THRESHOLD), (ip_key, _IP_FAIL_THRESHOLD)):
            row = (
                await s.execute(select(LoginThrottle).where(LoginThrottle.throttle_key == key))
            ).scalar_one_or_none()
            if row is None:
                row = LoginThrottle(throttle_key=key, fail_count=1)
                s.add(row)
            else:
                row.fail_count = (row.fail_count or 0) + 1
            if row.fail_count >= threshold and (row.locked_until is None or row.locked_until <= now):
                row.locked_until = now + timedelta(seconds=_lock_seconds(row.fail_count, threshold))
        await s.commit()


async def _reset_throttle(username: str, ip: str) -> None:
    """登录成功 → 清零该用户名+IP 与纯 IP 计数。"""
    from app.db.session import async_session
    from app.models.user import LoginThrottle

    async with async_session() as s:
        await s.execute(
            delete(LoginThrottle).where(
                LoginThrottle.throttle_key.in_(
                    [f"u:{username.lower()}|ip:{ip}", f"ip:{ip}"]
                )
            )
        )
        await s.commit()


class AuthService:
    async def login(
        self, db: AsyncSession, payload: LoginRequest, client_ip: str = "unknown"
    ) -> LoginResponse:
        # 锁定检查必须先于一切用户探测——锁定期内不给 timing/枚举任何信号
        await _check_throttle(payload.username, client_ip)

        user = await user_crud.get_by_username(db, payload.username)
        # 用户不存在 → 跑假 bcrypt；存在 → 跑真 bcrypt。响应时间一致。
        password_hash = user.password_hash if user else _DUMMY_HASH
        # bcrypt rounds=12 约 200-300ms 纯 CPU——放线程池，避免阻塞事件循环
        # （并发登录会串行化并拖慢同 worker 的所有请求）
        pwd_ok = await asyncio.to_thread(verify_password, payload.password, password_hash)
        await asyncio.sleep(0)  # 让出事件循环
        if user is None or not pwd_ok:
            await _record_fail(payload.username, client_ip)
            logger.warning("auth.login.fail username={} ip={}", payload.username, client_ip)
            raise AuthError("用户名或密码错误")
        if not user.is_active:
            raise AuthError("账号已被禁用")

        await _reset_throttle(payload.username, client_ip)
        token = create_access_token(
            subject=user.id,
            extra={"username": user.username, "is_superuser": user.is_superuser},
        )
        logger.info("auth.login.ok user_id={} username={}", user.id, user.username)
        return LoginResponse(
            access_token=token,
            expires_in=settings.JWT_EXPIRE_MINUTES * 60,
            user=UserOut.model_validate(user),
        )


auth_service = AuthService()
