"""角色 ORM 模型。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    code: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, nullable=False
    )
    remark: Mapped[str | None] = mapped_column(Text)

    # 注意：反向引用集合**不要**用 lazy="joined"，会触发 SQLAlchemy
    # "joined eager loads against collections" 报错。需要预加载时显式 selectinload。
    users: Mapped[list[User]] = relationship("User", back_populates="role")

    def __repr__(self) -> str:
        return f"<Role id={self.id} code={self.code}>"
