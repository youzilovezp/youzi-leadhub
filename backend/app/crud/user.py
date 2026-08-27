"""用户 CRUD。"""

from sqlalchemy import select

from app.core.exceptions import AuthError
from app.core.security import hash_password, verify_password
from app.crud.base import CRUDBase
from app.models.user import User
from app.schemas.user import UserCreate, UserPasswordUpdate, UserUpdate


class CRUDUser(CRUDBase[User, UserCreate, UserUpdate]):
    async def list_paginated(
        self, db, page: int = 1, page_size: int = 20, username=None, is_active=None
    ):
        """用户名模糊搜索（前端占位符承诺"模糊搜索"），其余字段精确匹配。"""
        from sqlalchemy import func, select

        stmt = select(User)
        count_stmt = select(func.count()).select_from(User)
        if username:
            stmt = stmt.where(User.username.ilike(f"%{username}%"))
            count_stmt = count_stmt.where(User.username.ilike(f"%{username}%"))
        if is_active is not None:
            stmt = stmt.where(User.is_active == is_active)
            count_stmt = count_stmt.where(User.is_active == is_active)
        total = (await db.execute(count_stmt)).scalar_one()
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        return list((await db.execute(stmt)).scalars().all()), total

    async def get_by_username(self, db, username: str) -> User | None:
        stmt = select(User).where(User.username == username)
        return (await db.execute(stmt)).scalar_one_or_none()

    async def get_by_email(self, db, email: str) -> User | None:
        stmt = select(User).where(User.email == email)
        return (await db.execute(stmt)).scalar_one_or_none()

    async def create(self, db, obj_in: UserCreate | dict) -> User:
        if isinstance(obj_in, dict):
            data = dict(obj_in)
        else:
            data = obj_in.model_dump()
        password = data.pop("password", None)
        if password is None:
            raise ValueError("UserCreate 必须提供 password")
        db_obj = User(**data, password_hash=hash_password(password))
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def update_password(
        self, db, user: User, payload: UserPasswordUpdate
    ) -> User:
        if not verify_password(payload.old_password, user.password_hash):
            raise AuthError("旧密码错误")
        user.password_hash = hash_password(payload.new_password)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    async def set_password(self, db, user: User, new_password: str) -> User:
        """管理员直接改密（不校验旧密码）。"""
        user.password_hash = hash_password(new_password)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


user_crud = CRUDUser(User)
