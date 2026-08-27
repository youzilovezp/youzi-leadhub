"""任务执行器：asyncio 后台执行、并发闸门（满则排队）、取消、超时。

- DB 是队列的唯一事实源：worker 轮询 status='queued' 按 id FIFO 认领
  （UPDATE ... RETURNING，重启不丢任务）
- COLLECT_MAX_CONCURRENT 个 worker 协程 = 并发上限
- 进程重启时把中断的 running 任务重置为 failed（start() 里做）
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select, update

from app.collectors import get_collector
from app.collectors.base import LeadDraft, TaskContext
from app.core.config import settings
from app.db.session import async_session
from app.models.collect_task import CollectTask, CollectTaskLog


class TaskRunner:
    def __init__(self) -> None:
        self._workers: list[asyncio.Task] = []
        self._cancel_events: dict[int, asyncio.Event] = {}
        self._progress: dict[int, tuple[int, int]] = {}  # task_id -> (total, done)
        self._stopping = False

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        # 中断的 running 任务重置为 failed（文档约定）
        async with async_session() as s:
            await s.execute(
                update(CollectTask)
                .where(CollectTask.status == "running")
                .values(
                    status="failed",
                    error="进程重启，任务中断",
                    finished_at=datetime.now(timezone.utc),
                )
            )
            await s.commit()
        self._stopping = False
        for i in range(settings.COLLECT_MAX_CONCURRENT):
            self._workers.append(asyncio.create_task(self._worker(i), name=f"collect-worker-{i}"))
        logger.info(f"✅ TaskRunner 已启动（并发 {settings.COLLECT_MAX_CONCURRENT}）")

    async def stop(self) -> None:
        self._stopping = True
        for t in self._workers:
            t.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    # ---------- 对外操作 ----------

    async def enqueue(self, task_id: int) -> None:
        """pending → queued（已在队列/运行中的不动）。"""
        async with async_session() as s:
            result = await s.execute(
                update(CollectTask)
                .where(
                    CollectTask.id == task_id,
                    CollectTask.status.in_(["pending", "cancelled", "completed", "failed"]),
                )
                .values(status="queued", error=None)
                .returning(CollectTask.id)
            )
            await s.commit()
            if result.scalar_one_or_none() is None:
                logger.debug(f"任务 {task_id} 未入队（状态非可排队）")

    async def cancel(self, task_id: int) -> bool:
        """排队中 → 直接 cancelled；运行中 → 置取消事件，worker 收尾。"""
        async with async_session() as s:
            result = await s.execute(
                update(CollectTask)
                .where(CollectTask.id == task_id, CollectTask.status == "queued")
                .values(status="cancelled", finished_at=datetime.now(timezone.utc))
                .returning(CollectTask.id)
            )
            await s.commit()
            if result.scalar_one_or_none() is not None:
                return True
        event = self._cancel_events.get(task_id)
        if event is not None:
            event.set()
            return True
        return False

    # ---------- worker ----------

    async def _worker(self, index: int) -> None:
        """认领最早的 queued 任务并执行。"""
        while not self._stopping:
            task_id = await self._claim()
            if task_id is None:
                await asyncio.sleep(1.0)
                continue
            try:
                await self._execute(task_id)
            except Exception:  # noqa: BLE001  worker 绝不能死
                logger.exception(f"任务 {task_id} 执行器异常")

    async def _claim(self) -> int | None:
        async with async_session() as s:
            # 按 id FIFO 认领；SQLite 3.35+ / PG 都支持 UPDATE..RETURNING
            row = (
                await s.execute(
                    select(CollectTask.id)
                    .where(CollectTask.status == "queued")
                    .order_by(CollectTask.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            result = await s.execute(
                update(CollectTask)
                .where(CollectTask.id == row, CollectTask.status == "queued")
                .values(status="running", started_at=datetime.now(timezone.utc))
                .returning(CollectTask.id)
            )
            await s.commit()
            return result.scalar_one_or_none()

    async def _execute(self, task_id: int) -> None:
        async with async_session() as s:
            task = await s.get(CollectTask, task_id)
            if task is None:
                return
            collector = get_collector(task.collector)
            params = dict(task.params or {})

        if collector is None:
            await self._finish(task_id, "failed", error=f"未知采集器：{task.collector}")
            return

        cancel_event = asyncio.Event()
        self._cancel_events[task_id] = cancel_event
        counters = {"added": 0, "merged": 0}

        async def emit(draft: LeadDraft) -> tuple[int, bool]:
            if not draft.name or not draft.name.strip():
                return 0, False
            async with async_session() as s:
                lead, created = await _upsert(s, draft)
                await s.commit()
            counters["added" if created else "merged"] += 1
            return lead.id, created

        async def log(level: str, message: str) -> None:
            async with async_session() as s:
                s.add(CollectTaskLog(task_id=task_id, level=level, message=message[:2000]))
                await s.commit()

        def set_total(total: int) -> None:
            self._progress[task_id] = (total, self._progress.get(task_id, (0, 0))[1])

        def inc_progress(delta: int) -> None:
            total, done = self._progress.get(task_id, (0, 0))
            done += delta
            self._progress[task_id] = (total, done)

        ctx = TaskContext(
            task_id=task_id,
            params=params,
            emit=emit,
            log=log,
            set_total=set_total,
            inc_progress=inc_progress,
            _cancel_event=cancel_event,
        )

        status, error = "completed", None
        try:
            await asyncio.wait_for(collector.run(ctx), timeout=settings.COLLECT_TASK_TIMEOUT)
        except asyncio.CancelledError:
            status = "cancelled"
        except TimeoutError:
            status = "failed"
            error = f"任务超时（{settings.COLLECT_TASK_TIMEOUT}s）"
        except Exception as exc:  # noqa: BLE001  业务异常 → 任务 failed，不炸 worker
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"[:1000]
            logger.warning(f"任务 {task_id} 失败：{error}")
        finally:
            self._cancel_events.pop(task_id, None)

        # 进度/计数落库（执行期间只写内存，收尾一次写）
        total, done = self._progress.pop(task_id, (0, 0))
        await self._finish(
            task_id,
            status,
            error=error,
            total=total,
            done=done,
            added=counters["added"],
            merged=counters["merged"],
        )
        if status == "completed":
            await log("info", f"任务完成：新增 {counters['added']}，合并 {counters['merged']}")

    async def _finish(
        self,
        task_id: int,
        status: str,
        *,
        error: str | None = None,
        total: int = 0,
        done: int = 0,
        added: int = 0,
        merged: int = 0,
    ) -> None:
        async with async_session() as s:
            task = await s.get(CollectTask, task_id)
            if task is None:
                return
            task.status = status
            task.error = error
            task.progress_total = total
            task.progress_done = done
            task.leads_added = added
            task.leads_merged = merged
            task.finished_at = datetime.now(timezone.utc)
            task.last_run_at = task.finished_at
            await s.commit()


async def _upsert(s, draft: LeadDraft):
    from app.crud.lead import upsert_lead

    return await upsert_lead(s, draft)


task_runner = TaskRunner()
