"""
认证相关业务逻辑。
"""

import asyncio

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AuthError
from app.core.security import create_access_token, hash_password, verify_password
from app.crud.user import user_crud
from app.schemas.user import LoginRequest, LoginResponse, UserOut

# 防御 timing attack：用户名不存在时仍跑一次假 bcrypt，让"用户不存在"和"密码错"
# 的响应时间差异降到最小（差异仅来自 SELECT 查询本身，几百微秒级）。
_DUMMY_HASH = hash_password("__timing_attack_defense__")


class AuthService:
    async def login(self, db: AsyncSession, payload: LoginRequest) -> LoginResponse:
        user = await user_crud.get_by_username(db, payload.username)
        # 用户不存在 → 跑假 bcrypt；存在 → 跑真 bcrypt。响应时间一致。
        # 异步里加一毫秒 sleep 进一步抹平 race condition
        password_hash = user.password_hash if user else _DUMMY_HASH
        pwd_ok = verify_password(payload.password, password_hash)
        await asyncio.sleep(0)  # 让出事件循环，避免阻塞其他请求
        if user is None or not pwd_ok:
            logger.warning("auth.login.fail username={}", payload.username)
            raise AuthError("用户名或密码错误")
        if not user.is_active:
            raise AuthError("账号已被禁用")

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
