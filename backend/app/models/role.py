"""角色 ORM 模型。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User

# 权限码词表（PRD §42 RBAC）：角色拥有权限码集合，超管旁路全部
# lead:read 看线索 / lead:write 录入·跟进·联系人 / lead:delete 删线索
# assign:lead 分配/转移/释放线索 / task:manage 采集任务管控
# user:manage 用户与角色管理 / stats:read 全局统计与排行榜
PERMISSION_CODES: list[str] = [
    "lead:read",
    "lead:write",
    "lead:delete",
    "assign:lead",
    "task:manage",
    "user:manage",
    "stats:read",
]

# PRD §42 角色种子（权限码的唯一样本源；init_db 与迁移 d2b1e98f091f 共同口径）。
# 超管走 is_superuser 旁路，不依赖角色；admin 角色仍配全量权限以防降级误用。
ROLE_SEEDS: list[tuple[str, str, list[str]]] = [
    ("admin", "管理员", list(PERMISSION_CODES)),
    ("sales_manager", "销售主管", ["lead:read", "lead:write", "assign:lead", "stats:read"]),
    ("sales", "销售", ["lead:read", "lead:write"]),
    ("operator", "运营", ["lead:read", "task:manage", "stats:read"]),
    ("data_admin", "数据管理员", ["lead:read", "stats:read"]),
]


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    code: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, nullable=False
    )
    remark: Mapped[str | None] = mapped_column(Text)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)  # 权限码列表，见 PERMISSION_CODES

    # 注意：反向引用集合**不要**用 lazy="joined"，会触发 SQLAlchemy
    # "joined eager loads against collections" 报错。需要预加载时显式 selectinload。
    users: Mapped[list[User]] = relationship("User", back_populates="role")

    def __repr__(self) -> str:
        return f"<Role id={self.id} code={self.code}>"
