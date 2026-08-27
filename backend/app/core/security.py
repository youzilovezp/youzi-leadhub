"""
安全相关：JWT（pyjwt）+ 密码哈希（bcrypt）+ Redis 黑名单。

从 python-jose 迁移到 PyJWT（pyjwt 维护更活跃，规避 CVE-2024-33663 等已知漏洞）。
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

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
    """无 Redis：没有黑名单存储，实际无法撤销 token。

    返回 False——让 logout 端点如实提示"撤销未生效"，而不是谎报已撤销。
    生产必须启用 Redis（--with-redis + REDIS_HOST）。
    """
    return False


async def is_token_blacklisted(jti: str) -> bool:
    return False

