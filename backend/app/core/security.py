"""
安全相关：JWT（pyjwt）+ 密码哈希（bcrypt）+ DB 黑名单。

从 python-jose 迁移到 PyJWT（pyjwt 维护更活跃，规避 CVE-2024-33663 等已知漏洞）。
token 撤销用 token_blacklist 表（jti 唯一索引点查）——不引入 Redis 依赖，
跨进程（多 worker）语义一致。
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from sqlalchemy import delete, select

from app.core.config import settings


def hash_password(plain: str) -> str:
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(
    subject: str | int,
    expires_delta: timedelta | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """生成带 jti 的 JWT（jti 用于黑名单撤销）。"""
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    )
    payload: dict[str, Any] = {
        "sub": str(subject),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": secrets.token_hex(16),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except jwt.PyJWTError as exc:
        raise ValueError(f"无效的 token: {exc}") from exc



async def blacklist_token(token: str) -> bool:
    """撤销 token：解析出 jti/exp 写入 token_blacklist 表，剩余有效期内 401。

    - token 已过期/非法 → False（无需撤销，本来就会 401）
    - jti 已在库 → 幂等成功
    - 写入时顺带清理已过期行（行量与活跃 token 数同阶，不会膨胀）
    """
    from app.db.session import async_session
    from app.models.user import TokenBlacklist

    try:
        payload = decode_token(token)
    except ValueError:
        return False
    jti = payload.get("jti")
    exp = payload.get("exp")
    sub = payload.get("sub")
    if not jti or not isinstance(exp, int | float):
        return False
    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        return False  # 已过期，撤销无意义

    user_id: int | None = None
    try:
        user_id = int(sub) if sub is not None else None
    except (TypeError, ValueError):
        user_id = None

    try:
        async with async_session() as session:
            # 清理过期行（点查索引扫描，行数有限）
            await session.execute(
                delete(TokenBlacklist).where(TokenBlacklist.expires_at <= datetime.now(timezone.utc))
            )
            existing = (
                await session.execute(select(TokenBlacklist.id).where(TokenBlacklist.jti == jti))
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    TokenBlacklist(jti=jti, user_id=user_id, expires_at=expires_at)
                )
            await session.commit()
        return True
    except Exception:  # noqa: BLE001  DB 故障时如实返回未撤销，不阻断登出
        return False


async def is_token_blacklisted(jti: str) -> bool:
    """jti 是否已撤销（每请求一次点查；空 jti 的旧 token 直接放行由 exp 兜底）。"""
    if not jti:
        return False
    from app.db.session import async_session
    from app.models.user import TokenBlacklist

    async with async_session() as session:
        row = (
            await session.execute(
                select(TokenBlacklist.id).where(
                    TokenBlacklist.jti == jti,
                    TokenBlacklist.expires_at > datetime.now(timezone.utc),
                )
            )
        ).scalar_one_or_none()
    return row is not None

