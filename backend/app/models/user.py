"""用户 ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.role import Role


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, nullable=False
    )
    email: Mapped[str | None] = mapped_column(String(120), unique=True, index=True)
    nickname: Mapped[str | None] = mapped_column(String(50))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    avatar: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(20))

    # ---------- 数据权限（PRD §43 三级）：all=公司级看全部 / team=团队级 / own=个人级 ----------
    data_scope: Mapped[str] = mapped_column(
        String(16), default="all", server_default="all", index=True
    )
    team: Mapped[str | None] = mapped_column(String(64), index=True)  # 团队标识（team 级数据权限的分组键）

    role_id: Mapped[int | None] = mapped_column(
        ForeignKey("roles.id", ondelete="SET NULL"), index=True
    )
    role: Mapped[Role | None] = relationship(
        "Role", back_populates="users", lazy="joined"
    )

    @property
    def role_name(self) -> str | None:
        """前端 UserOut.role_name 字段——避免模板里手写 IF。"""
        return self.role.name if self.role is not None else None

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username}>"


class TokenBlacklist(Base):
    """已撤销 JWT（登出即失效；DB 存储无 Redis 依赖，跨进程有效）。

    行按 token 的 exp 自然过期，blacklist_token 写入时顺带清理过期行。
    deps.get_current_user 每请求按 jti 查一次（唯一索引，单行点查）。
    """

    __tablename__ = "token_blacklist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LoginThrottle(Base):
    """登录失败计数与锁定（暴力破解防护，DB 存储跨进程有效）。

    throttle_key 两类：`u:<username>|ip:<ip>`（按用户+来源，5 次锁）与
    `ip:<ip>`（纯来源，20 次锁——防同一 IP 撒网式试多个用户名）。
    锁定时长指数退避（60s 起倍增，封顶 1 小时），成功登录清零。
    """

    __tablename__ = "login_throttles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    throttle_key: Mapped[str] = mapped_column(String(191), unique=True, index=True, nullable=False)
    fail_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
