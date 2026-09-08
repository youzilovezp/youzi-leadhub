"""调度器回归测试。

2026-09-01 SCHEDULER_ENABLED=true 首启即崩：sync() 注册 _fire 时漏传
args=[task_id]，APScheduler check_callable_args 抛 ValueError（此前调度
开关一直 false，该路径从未被执行——测试库补上这条链路的守卫）。
"""

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.services import scheduler as sched


@pytest.mark.asyncio
async def test_sync_registers_job_with_task_id_arg(db_session):
    """cron 任务行 → sync() → job 注册成功且携带 task_id 参数。"""
    from app.db.init_db import init_db
    from app.models.collect_task import CollectTask

    await init_db()  # db_session fixture 不建表，这里补
    task = CollectTask(name="test-cron", collector="meta_ads", params={}, cron_expr="0 9 * * *")
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    sched._scheduler = AsyncIOScheduler()
    sched._scheduler.start()
    try:
        await sched.sync()
        job = sched._scheduler.get_job(str(task.id))
        assert job is not None, "cron 任务应注册为 job"
        assert job.args == (str(task.id),), "job 必须携带 task_id（否则触发时 TypeError）"
    finally:
        sched._scheduler.shutdown(wait=False)
        sched._scheduler = None


@pytest.mark.asyncio
async def test_sync_skips_disabled_and_non_cron(db_session):
    """disabled 或 cron_expr 为空的任务不注册 job。"""
    from sqlalchemy import delete

    from app.db.init_db import init_db
    from app.models.collect_task import CollectTask

    await init_db()  # db_session fixture 不建表，这里补
    await db_session.execute(delete(CollectTask))  # 清空共享测试库的既有 cron 行
    await db_session.commit()
    db_session.add(
        CollectTask(name="disabled", collector="meta_ads", params={}, cron_expr="0 8 * * *", enabled=False)
    )
    db_session.add(CollectTask(name="manual", collector="web_search", params={}, cron_expr=None))
    await db_session.commit()

    sched._scheduler = AsyncIOScheduler()
    sched._scheduler.start()
    try:
        await sched.sync()
        ids = {j.id for j in sched._scheduler.get_jobs()}
        assert not ids, f"不应注册任何 job，实际：{ids}"
    finally:
        sched._scheduler.shutdown(wait=False)
        sched._scheduler = None
