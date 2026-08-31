"""任务执行器：asyncio 后台执行、并发闸门（满则排队）、取消、超时。

- DB 是队列的唯一事实源：worker 轮询 status='queued' 按 id FIFO 认领
  （UPDATE ... RETURNING，重启不丢任务）
- COLLECT_MAX_CONCURRENT 个 worker 协程 = 并发上限
- 进程重启时把中断的 running 任务重置为 failed（start() 里做）
"""

from __future__ import annotations

import asyncio
import time
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
        self._progress_synced_at: dict[int, float] = {}  # task_id -> 上次落库时间戳
        self._stopping = False

    # ---------- 采集流水线自动接力（2026-08-31 交互改造） ----------
    # 发现类采集器产出的种子/线索要靠 website_enrich 才能出信号、过 ICP 门、
    # 进评分——这个先后依赖以前要用户自己知道并手动跑两步。改为系统承担：
    # 发现类任务成功完成 → 自动排入隐式富化复核任务（官网发现 + 分级重爬）。

    _CHAIN_ENRICH_AFTER = frozenset({"web_search", "job_posting", "meta_ads"})

    async def _maybe_chain_enrich(
        self, log_fn, task_id: int, collector: str, created_by: int | None
    ) -> None:
        """发现类任务完成后自动接力 website_enrich（失败不影响主任务）。

        去重只看「有没有全库富化（params 为空）还在 pending/queued」——排队中的
        那次扫描发生在本批线索入库之后，必然覆盖。刚跑完的不算数：它扫不到本次
        新增的线索（2026-08-31 修复：旧的 60 分钟时间窗口会整批漏富化）；多余的
        接力也只是分级增量的廉价空扫。勾选型（lead_ids）排队不算，它不扫全库。
        """
        if not settings.AUTO_CHAIN_ENRICH or collector not in self._CHAIN_ENRICH_AFTER:
            return
        try:
            async with async_session() as s:
                # 排队中的富化任务本就只有个位数，全取回内存里判「全库扫」——
                # 不在 SQL 里比较 JSON（PG json 类型无 = 操作符，2026-08-31 e2e 实测）
                pending_rows = (
                    (
                        await s.execute(
                            select(CollectTask.id, CollectTask.params).where(
                                CollectTask.collector == "website_enrich",
                                CollectTask.status.in_(["pending", "queued"]),
                            )
                        )
                    )
                    .all()
                )
                full_scan = next(
                    (row_id for row_id, row_params in pending_rows if not (row_params or {})), None
                )
                if full_scan is not None:
                    await log_fn(
                        "info",
                        f"↪️ 已跳过自动富化接力（全库富化任务 #{full_scan} 排队中，会覆盖本次新增线索）",
                    )
                    return
                chained = CollectTask(
                    name=f"🔁 自动富化复核（由 {collector} 任务 #{task_id} 触发）",
                    collector="website_enrich",
                    params={},
                    is_implicit=True,
                    created_by=created_by,
                )
                s.add(chained)
                await s.flush()
                chained_id = chained.id
                await s.commit()
            await self.enqueue(chained_id)
            await log_fn(
                "info",
                f"✅ 已自动接力网站富化任务 #{chained_id}（官网发现 + 信号复核 + 重评分）——"
                "发现类采集完成后的依赖步骤无需手动执行",
            )
        except Exception:  # noqa: BLE001  接力是增强体验，失败绝不影响主任务结果
            logger.exception(f"任务 {task_id} 自动富化接力失败（忽略）")

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
                    # 进度/计数是上一次执行中断时的半截残留，清零避免详情页误导
                    progress_total=0,
                    progress_done=0,
                    leads_added=0,
                    leads_merged=0,
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
            # 运行中但 cancel event 还没注册（claim 与注册之间的窗口）：
            # 预置已触发的 event，worker claim 时用 setdefault 不会覆盖它
            status = (
                await s.execute(select(CollectTask.status).where(CollectTask.id == task_id))
            ).scalar_one_or_none()
        if status == "running":
            self._cancel_events.setdefault(task_id, asyncio.Event()).set()
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
            # 接力元信息趁会话开着取（detached 实例属性访问不可靠）
            chain_collector, chain_created_by = task.collector, task.created_by

        if collector is None:
            await self._finish(task_id, "failed", error=f"未知采集器：{task.collector}")
            return

        # setdefault：cancel() 可能在注册前预置了已触发的 event，不能覆盖
        cancel_event = self._cancel_events.setdefault(task_id, asyncio.Event())
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
            # 节流落库：进度只在收尾写库的话，长任务运行期间详情页恒 0；
            # 每条写库又放大事务。折中——5 秒一次，重启最多丢 5 秒进度。
            now = time.monotonic()
            last = self._progress_synced_at.get(task_id, 0.0)
            if now - last >= 5.0:
                self._progress_synced_at[task_id] = now
                asyncio.create_task(self._sync_progress(task_id, total, done))

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

        # 进度/计数落库（执行期间节流写，收尾一次写终值）
        total, done = self._progress.pop(task_id, (0, 0))
        self._progress_synced_at.pop(task_id, None)
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
            # 流水线自动接力：发现类采集 → 富化复核（系统承担依赖顺序）
            await self._maybe_chain_enrich(log, task_id, chain_collector, chain_created_by)

    async def _sync_progress(self, task_id: int, total: int, done: int) -> None:
        """运行中节流落库进度（失败静默——进度展示不值得炸协程）。"""
        try:
            async with async_session() as s:
                await s.execute(
                    update(CollectTask)
                    .where(CollectTask.id == task_id, CollectTask.status == "running")
                    .values(progress_total=total, progress_done=done)
                )
                await s.commit()
        except Exception:  # noqa: BLE001
            logger.debug(f"任务 {task_id} 进度落库失败（忽略）")

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
