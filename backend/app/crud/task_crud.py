"""采集任务 CRUD。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.base import CRUDBase
from app.models.collect_task import CollectTask
from app.schemas.collect import TaskCreate, TaskUpdate

task_crud = CRUDBase[CollectTask, TaskCreate, TaskUpdate](CollectTask)


async def list_tasks(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    collector: str | None = None,
    status: str | None = None,
) -> tuple[list[CollectTask], int]:
    """任务列表：CRUDBase 无排序（展示顺序不稳定），这里固定最新在前。"""
    stmt = select(CollectTask)
    count_stmt = select(func.count()).select_from(CollectTask)
    for field, value in (("collector", collector), ("status", status)):
        if value:
            stmt = stmt.where(getattr(CollectTask, field) == value)
            count_stmt = count_stmt.where(getattr(CollectTask, field) == value)
    total = (await db.execute(count_stmt)).scalar_one()
    stmt = stmt.order_by(CollectTask.id.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list((await db.execute(stmt)).scalars().all())
    return items, total
