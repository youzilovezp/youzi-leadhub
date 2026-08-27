"""用户管理接口。"""

from fastapi import APIRouter, Query

from app.api.deps import SessionDep, SuperUser
from app.core.exceptions import BusinessError, NotFoundError
from app.crud.user import user_crud
from app.schemas.common import PageResponse, ResponseModel
from app.schemas.user import (
    AdminPasswordUpdate,
    UserCreate,
    UserOut,
    UserUpdate,
)

router = APIRouter()


@router.get("", response_model=ResponseModel[PageResponse[UserOut]], summary="用户列表")
async def list_users(
    db: SessionDep,
    _user: SuperUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    username: str | None = None,
    is_active: bool | None = None,
):
    items, total = await user_crud.list_paginated(
        db, page=page, page_size=page_size, username=username, is_active=is_active
    )
    return ResponseModel(
        data=PageResponse[UserOut](
            items=[UserOut.model_validate(u) for u in items],
            total=total,
            page=page,
            page_size=page_size,
        )
    )


@router.post("", response_model=ResponseModel[UserOut], summary="创建用户")
async def create_user(db: SessionDep, _user: SuperUser, payload: UserCreate):
    if await user_crud.get_by_username(db, payload.username):
        raise BusinessError(code=40001, message="用户名已存在")
    # email 是 DB unique 列：先查重，避免 IntegrityError 变 500
    if payload.email and await user_crud.get_by_email(db, payload.email):
        raise BusinessError(code=40001, message="邮箱已被使用")
    # role_id 是外键：不校验会 FK 违约变 500
    if payload.role_id is not None:
        from app.crud.role import role_crud

        if await role_crud.get(db, payload.role_id) is None:
            raise BusinessError(code=40001, message="角色不存在")
    user = await user_crud.create(db, payload)
    return ResponseModel(data=UserOut.model_validate(user))


@router.get("/{user_id}", response_model=ResponseModel[UserOut], summary="用户详情")
async def get_user(db: SessionDep, _user: SuperUser, user_id: int):
    user = await user_crud.get(db, user_id)
    if user is None:
        raise NotFoundError("用户不存在")
    return ResponseModel(data=UserOut.model_validate(user))


@router.put("/{user_id}", response_model=ResponseModel[UserOut], summary="更新用户")
async def update_user(
    db: SessionDep,
    _user: SuperUser,
    user_id: int,
    payload: UserUpdate,
):
    target = await user_crud.get(db, user_id)
    if target is None:
        raise NotFoundError("用户不存在")
    if payload.email and payload.email != target.email:
        if await user_crud.get_by_email(db, payload.email):
            raise BusinessError(code=40001, message="邮箱已被使用")
    if payload.role_id is not None:
        from app.crud.role import role_crud

        if await role_crud.get(db, payload.role_id) is None:
            raise BusinessError(code=40001, message="角色不存在")
    updated = await user_crud.update(db, target, payload)
    return ResponseModel(data=UserOut.model_validate(updated))


@router.delete("/{user_id}", response_model=ResponseModel, summary="删除用户")
async def delete_user(db: SessionDep, user: SuperUser, user_id: int):
    if user_id == user.id:
        raise BusinessError(message="不能删除自己")
    # 修复：之前没检查"最后一名超级管理员"——攻击者 / 误操作删掉最后一个
    # superuser 后整个系统永久锁死。
    target = await user_crud.get(db, user_id)
    if target is None:
        raise NotFoundError("用户不存在")
    if target.is_superuser:
        from sqlalchemy import func, select

        from app.models.user import User as UserModel

        cnt = (
            await db.execute(
                select(func.count())
                .select_from(UserModel)
                .where(UserModel.is_superuser.is_(True))
            )
        ).scalar_one()
        if cnt <= 1:
            raise BusinessError(message="不能删除最后一个超级管理员")
    if not await user_crud.delete(db, user_id):
        raise NotFoundError("用户不存在")
    return ResponseModel(message="已删除")


@router.post(
    "/{user_id}/password",
    response_model=ResponseModel,
    summary="修改密码（管理员）",
)
async def admin_change_password(
    db: SessionDep,
    _user: SuperUser,
    user_id: int,
    payload: AdminPasswordUpdate,
):
    """管理员直接重置用户密码（不需要旧密码）。

    用 AdminPasswordUpdate 而非 UserPasswordUpdate——后者强制要求 old_password，
    实际管理员改密场景下没有"旧密码"语义。
    """
    target = await user_crud.get(db, user_id)
    if target is None:
        raise NotFoundError("用户不存在")
    await user_crud.set_password(db, target, payload.new_password)
    return ResponseModel(message="密码已更新")
