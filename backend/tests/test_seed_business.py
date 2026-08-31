"""业务种子（一次性初始化）：空库导入 / 非空跳过 / 开关关闭。

隔离方式：共享测试库 leads 可能非空，本文件用「先清空 leads → 种子 → 断言 →
再清空」的自洽清理；AUTO_SEED_BUSINESS 在 conftest 已全局关闭，这里直接调
seed_business_data 并用 monkeypatch 打开开关。
"""

from sqlalchemy import delete, func, select

from app.db.init_db import seed_business_data
from app.db.seed_data import SEED_LEAD_COUNT, SEED_PAYLOAD
from app.models.collect_task import CollectTask
from app.models.lead import Lead


async def test_seed_business_once_and_skip(db_session, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AUTO_SEED_BUSINESS", True)
    from app.db.init_db import init_db  # noqa: F401  触发建表（幂等）

    await init_db()

    # 前置：清空 leads 与 cron 任务（共享库；测试结束恢复为空）
    await db_session.execute(delete(Lead))
    await db_session.execute(delete(CollectTask))
    await db_session.commit()

    # 1) 空库 → 导入种子
    await seed_business_data()
    n = (await db_session.execute(select(func.count()).select_from(Lead))).scalar_one()
    assert n == SEED_LEAD_COUNT == 93
    # 无重复 dedupe_key（去重链路正确）
    dups = (
        await db_session.execute(
            select(func.count())
            .select_from(
                select(Lead.dedupe_key, func.count().label("c"))
                .group_by(Lead.dedupe_key)
                .having(func.count() > 1)
                .subquery()
            )
        )
    ).scalar_one()
    assert dups == 0
    # 全部中国企业证据链（ICP 门内）
    icp = dict(
        (await db_session.execute(select(Lead.icp_status, func.count()).group_by(Lead.icp_status))).all()
    )
    assert set(icp) == {"qualified", "cn_domestic"}
    # 任务按 collector 建齐
    collectors = {
        c for (c,) in (await db_session.execute(select(CollectTask.collector))).all()
    }
    assert {t["collector"] for t in SEED_PAYLOAD["tasks"]} <= collectors

    # 2) 非空 → 跳过（只初始化一次）
    await seed_business_data()
    n2 = (await db_session.execute(select(func.count()).select_from(Lead))).scalar_one()
    assert n2 == n

    # 3) 开关关闭 → 跳过
    await db_session.execute(delete(Lead))
    await db_session.commit()
    monkeypatch.setattr(settings, "AUTO_SEED_BUSINESS", False)
    await seed_business_data()
    n3 = (await db_session.execute(select(func.count()).select_from(Lead))).scalar_one()
    assert n3 == 0

    # 清理（共享测试库）：任务也删掉，避免影响其他文件的任务列表断言
    await db_session.execute(delete(CollectTask))
    await db_session.commit()
