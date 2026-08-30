"""采集任务 ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_class import Base, TimestampMixin


class CollectTask(Base, TimestampMixin):
    __tablename__ = "collect_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    collector: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # 定时：5 段 crontab（如 "0 9 * * *"），空 = 只手动执行
    cron_expr: Mapped[str | None] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 隐式任务：线索列表勾选「检测 WhatsApp」自动创建，不在任务列表默认展示噪音
    is_implicit: Mapped[bool] = mapped_column(Boolean, default=False)
    # 操作人：谁创建的任务（显式建任务 / 勾选检测的当前用户）
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    # pending（已建未排队）/ queued（排队中）/ running / completed / failed / cancelled
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    leads_added: Mapped[int] = mapped_column(Integer, default=0)
    leads_merged: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(1024))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<CollectTask id={self.id} {self.collector} status={self.status}>"


class CollectTaskLog(Base):
    """任务执行日志（任务详情页轮询展示）。"""

    __tablename__ = "collect_task_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("collect_tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    level: Mapped[str] = mapped_column(String(8), default="info")  # info / warn / error
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
