"""权限依赖（PRD §42 RBAC + §43 数据权限）。

角色持有权限码（roles.permissions JSON），超级管理员旁路全部校验。
与 deps.py 的 CurrentUser/SuperUser 并存：SuperUser 语义 ≈ admin 级旁路。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.api.deps import CurrentUser
from app.core.exceptions import PermissionDeniedError
from app.models.role import PERMISSION_CODES
from app.models.user import User

__all__ = ["PERMISSION_CODES", "require_permission", "user_permission_codes", "DataScope"]


def user_permission_codes(user: User) -> set[str]:
    """用户生效权限码：超管全量，否则取角色 permissions。"""
    if user.is_superuser:
        return set(PERMISSION_CODES)
    if user.role is None:
        return set()
    return {p for p in (user.role.permissions or []) if p in PERMISSION_CODES}


def require_permission(code: str):
    """FastAPI 依赖工厂：校验当前用户持有权限码，返回 User。

    用法：user: Annotated[User, Depends(require_permission("task:manage"))]
    """

    async def _dep(user: CurrentUser) -> User:
        if code not in user_permission_codes(user):
            raise PermissionDeniedError(message=f"缺少权限：{code}")
        return user

    return _dep


def UserWithPermission(code: str):  # noqa: N802 依赖工厂命名为类型风格便于阅读
    """require_permission 的别名形态，端点签名更直观。"""
    return Annotated[User, Depends(require_permission(code))]


class DataScope:
    """数据权限（§43）：all=公司级 / team=团队级 / own=个人级（+共享池可认领）。

    超管与未设置的用户默认 all；search_leads 据 owner 条件强制过滤，
    绕过方式只有改库——接口层无旁路。
    """

    def __init__(self, user: User):
        self.user = user
        mode = getattr(user, "data_scope", "all") or "all"
        if user.is_superuser:
            mode = "all"
        self.mode = mode if mode in ("all", "team", "own") else "all"

    @property
    def is_restricted(self) -> bool:
        return self.mode != "all"


async def scope_filter_params(db, user: User) -> tuple[list[int] | None, bool]:
    """计算 search_leads 的数据权限参数 (scope_owner_ids, include_unassigned)。

    - all → (None, True) 不加过滤
    - own → ([user.id], True) 自己 + 共享池
    - team → (同 team 成员 id 列表, True)
    """
    scope = DataScope(user)
    if not scope.is_restricted:
        return None, True
    if scope.mode == "own":
        return [user.id], True
    from sqlalchemy import select

    from app.models.user import User as UserModel

    rows = (
        await db.execute(
            select(UserModel.id).where(
                UserModel.team == user.team, UserModel.is_active, UserModel.team.is_not(None)
            )
        )
    ).all()
    return sorted({user.id, *(r[0] for r in rows)}), True


def lead_visible(lead_owner_id: int | None, scope_owner_ids: list[int] | None) -> bool:
    """详情/操作前的可见性校验（与列表口径一致：受限范围 + 共享池）。"""
    if scope_owner_ids is None:
        return True
    return lead_owner_id is None or lead_owner_id in scope_owner_ids
