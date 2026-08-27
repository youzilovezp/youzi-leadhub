"""角色管理接口。"""

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import SessionDep, SuperUser
from app.core.exceptions import BusinessError, NotFoundError
from app.crud.role import role_crud
from app.models.user import User
from app.schemas.common import ResponseModel
from app.schemas.role import RoleCreate, RoleOut, RoleUpdate

router = APIRouter()


@router.get("", response_model=ResponseModel[list[RoleOut]], summary="角色列表")
async def list_roles(db: SessionDep, _user: SuperUser):
    items = await role_crud.list_all(db)
    return ResponseModel(data=[RoleOut.model_validate(r) for r in items])


@router.post("", response_model=ResponseModel[RoleOut], summary="创建角色")
async def create_role(db: SessionDep, _user: SuperUser, payload: RoleCreate):
    # 防重名（DB unique violation 会泄露表结构）
    existing = await role_crud.get_by(db, code=payload.code)
    if existing is not None:
        raise BusinessError(message=f"角色代码已存在：{payload.code}")
    role = await role_crud.create(db, payload)
    return ResponseModel(data=RoleOut.model_validate(role))


@router.get("/{role_id}", response_model=ResponseModel[RoleOut], summary="角色详情")
async def get_role(db: SessionDep, _user: SuperUser, role_id: int):
    role = await role_crud.get(db, role_id)
    if role is None:
        raise NotFoundError("角色不存在")
    return ResponseModel(data=RoleOut.model_validate(role))


@router.put("/{role_id}", response_model=ResponseModel[RoleOut], summary="更新角色")
async def update_role(
    db: SessionDep, _user: SuperUser, role_id: int, payload: RoleUpdate
):
    role = await role_crud.get(db, role_id)
    if role is None:
        raise NotFoundError("角色不存在")
    updated = await role_crud.update(db, role, payload)
    return ResponseModel(data=RoleOut.model_validate(updated))


@router.delete("/{role_id}", response_model=ResponseModel, summary="删除角色")
async def delete_role(db: SessionDep, _user: SuperUser, role_id: int):
    role = await role_crud.get(db, role_id)
    if role is None:
        raise NotFoundError("角色不存在")
    # 保护：内置 admin 角色（code="admin"）不可删
    # 用 code 而非 id 识别，避免 PK 不一致导致误判
    if role.code == "admin":
        raise BusinessError(message="不能删除内置 admin 角色")
    # 保护：有用户的角色不可删（FK ondelete=SET NULL 会把所有用户变成无角色）
    user_count = (
        await db.execute(
            select(func.count()).select_from(User).where(User.role_id == role_id)
        )
    ).scalar_one()
    if user_count > 0:
        raise BusinessError(
            message=f"该角色下还有 {user_count} 个用户，请先转移或删除用户"
        )
    if not await role_crud.delete(db, role_id):
        raise NotFoundError("角色不存在")
    return ResponseModel(message="已删除")
