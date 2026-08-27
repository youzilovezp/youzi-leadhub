"""
通用 CRUD 基类。

提供最常用的增删改查，业务层只继承即可。
"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base_class import Base

ModelType = TypeVar("ModelType", bound=Base)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)


class CRUDBase(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    def __init__(self, model: type[ModelType]):
        self.model = model

    async def get(self, db: AsyncSession, pk: Any) -> ModelType | None:
        return await db.get(self.model, pk)

    async def get_by(self, db: AsyncSession, **filters: Any) -> ModelType | None:
        """按字段查唯一记录（不存返回 None，多条返回首条）。"""
        from sqlalchemy import select

        if not filters:
            return None
        stmt = select(self.model)
        for field, value in filters.items():
            stmt = stmt.where(getattr(self.model, field) == value)
        return (await db.execute(stmt)).scalars().first()

    async def list_all(self, db: AsyncSession) -> list[ModelType]:
        stmt = select(self.model)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def list_paginated(
        self,
        db: AsyncSession,
        page: int = 1,
        page_size: int = 20,
        **filters: Any,
    ) -> tuple[list[ModelType], int]:
        from sqlalchemy import func

        stmt = select(self.model)
        count_stmt = select(func.count()).select_from(self.model)

        for field, value in filters.items():
            # 空字符串视为"不过滤"：前端表单常把空输入序列化成 ?xxx= 发过来
            if value is None or value == "":
                continue
            clause = getattr(self.model, field) == value
            stmt = stmt.where(clause)
            count_stmt = count_stmt.where(clause)

        total = (await db.execute(count_stmt)).scalar_one()
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        items = list((await db.execute(stmt)).scalars().all())
        return items, total

    async def create(
        self, db: AsyncSession, obj_in: CreateSchemaType | dict
    ) -> ModelType:
        data = obj_in.model_dump() if isinstance(obj_in, BaseModel) else obj_in
        db_obj = self.model(**data)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def update(
        self,
        db: AsyncSession,
        db_obj: ModelType,
        obj_in: UpdateSchemaType | dict,
    ) -> ModelType:
        data = (
            obj_in.model_dump(exclude_unset=True)
            if isinstance(obj_in, BaseModel)
            else obj_in
        )
        for field, value in data.items():
            setattr(db_obj, field, value)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def delete(self, db: AsyncSession, pk: Any) -> bool:
        obj = await db.get(self.model, pk)
        if obj is None:
            return False
        await db.delete(obj)
        await db.commit()
        return True
