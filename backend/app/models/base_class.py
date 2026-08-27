"""为避免循环引用，模型基类重新导出 db/base_class 中的 Base。"""

from app.db.base_class import Base, TimestampMixin

__all__ = ["Base", "TimestampMixin"]
