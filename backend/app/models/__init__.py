"""ORM 模型。"""

from app.models.base_class import Base
from app.models.collect_task import CollectTask, CollectTaskLog
from app.models.lead import Lead, LeadContact, LeadEvent, LeadFollowUp
from app.models.role import Role
from app.models.sales import Opportunity, SalesMessage
from app.models.user import User

__all__ = [
    "Base",
    "User",
    "Role",
    "Lead",
    "LeadFollowUp",
    "LeadContact",
    "LeadEvent",
    "Opportunity",
    "SalesMessage",
    "CollectTask",
    "CollectTaskLog",
]
