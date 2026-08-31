"""初始化三个每日 cron 任务（跨机器可复现；幂等——已存在同 collector 的 cron 则跳过）。

用法：cd backend && uv run python scripts/seed_cron_tasks.py
（seed_data.py 连同业务种子已在 2026-08-30 范围收缩中移除，本脚本是 cron 的事后补偿）
"""

import asyncio

from sqlalchemy import select

from app.db.session import async_session
from app.models.collect_task import CollectTask
from app.models.user import User

CRONS = [
    ("meta_ads", "Meta 广告库挖掘（每日）",
     {"keywords": "smart watch,leggings,wig,shapewear,led strip light,phone case,jewelry,game",
      "countries": "MY,SG,ID,TH,PH,VN,AE,SA", "probe_pages": "true", "max_pages": "2"},
     "30 2 * * *"),
    ("job_posting", "招聘信号巡检（每日）",
     {"site": "jobui", "keywords": "跨境电商客服,英语客服,海外社媒运营,私域运营,外贸业务员",
      "discover_new": "false"},
     "30 3 * * *"),
    ("website_enrich", "网站富化·全库分级（每日）", {}, "0 4 * * *"),
]


async def main() -> None:
    async with async_session() as s:
        admin = (await s.execute(select(User).where(User.username == "admin"))).scalar_one_or_none()
        for collector, name, params, cron in CRONS:
            exists = (
                await s.execute(
                    select(CollectTask).where(
                        CollectTask.collector == collector, CollectTask.cron_expr.is_not(None)
                    )
                )
            ).scalar_one_or_none()
            if exists:
                print(f"跳过（已存在 #{exists.id}）：{collector}")
                continue
            s.add(CollectTask(
                name=name, collector=collector, params=params, cron_expr=cron,
                is_implicit=False, created_by=admin.id if admin else None, status="pending",
            ))
            print(f"新建：{collector}（{cron}）")
        await s.commit()


if __name__ == "__main__":
    asyncio.run(main())
