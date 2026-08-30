"""APScheduler 定时调度：cron 任务存 DB，重启自动恢复。

- 只在 SCHEDULER_ENABLED=true 的进程启动（多 worker 只开一个）
- 触发动作 = runner.enqueue(task_id)（重置状态排队，闸门满自然排队）
- sync()：全量重建 job（任务创建/更新/删除后调用，简单粗暴不会漏）
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from sqlalchemy import select

from app.db.session import async_session
from app.models.collect_task import CollectTask
from app.services.task_runner import task_runner

_scheduler: AsyncIOScheduler | None = None


async def start() -> None:
    global _scheduler
    from app.core.config import settings

    if not settings.SCHEDULER_ENABLED:
        logger.info("⏭️  定时调度未开启（SCHEDULER_ENABLED=false）")
        return
    if settings.WORKERS > 1:
        # 防御性兜底：多进程同时开 cron 会重复入队/互相覆盖内存取消事件
        logger.warning("⏭️  WORKERS>1，定时调度未启动（单进程设计，防重复触发）")
        return
    # 不传 timezone：默认 tzlocal 本地时区。曾用 str(datetime.now().astimezone().tzinfo)，
    # 得到 "CST" 这类非 IANA 名，APScheduler 解析抛 ZoneInfoNotFoundError 启动即崩。
    scheduler = AsyncIOScheduler()
    scheduler.start()
    _scheduler = scheduler
    await sync()
    logger.info("✅ 定时调度已启动")


async def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


async def sync() -> None:
    """按 DB 全量重建 cron job（幂等）。"""
    if _scheduler is None:
        return
    async with async_session() as s:
        tasks = (
            (await s.execute(select(CollectTask).where(CollectTask.cron_expr.is_not(None))))
            .scalars()
            .all()
        )

    job_ids = {str(t.id) for t in tasks if t.enabled}
    for job in list(_scheduler.get_jobs()):
        if job.id not in job_ids:
            _scheduler.remove_job(job.id)

    for t in tasks:
        if not t.enabled:
            continue
        if _scheduler.get_job(str(t.id)) is not None:
            continue
        try:
            trigger = CronTrigger.from_crontab(t.cron_expr)  # type: ignore[arg-type]
        except ValueError:
            logger.warning(f"任务 {t.id} 的 cron 表达式非法，跳过：{t.cron_expr}")
            continue
        _scheduler.add_job(
            _fire,
            trigger=trigger,
            id=str(t.id),
            name=f"collect-task-{t.id}",
            replace_existing=True,
        )
    logger.info(f"📅 已加载 {len(job_ids)} 个定时采集任务")


async def _fire(task_id: str) -> None:
    """cron 触发：重置任务状态 + 入队（并发闸门满则排队）。"""
    async with async_session() as s:
        task = await s.get(CollectTask, int(task_id))
        if task is None or not task.enabled:
            return
    logger.info(f"⏰ 定时触发任务 {task_id}（{task.collector}）")
    await task_runner.enqueue(int(task_id))
