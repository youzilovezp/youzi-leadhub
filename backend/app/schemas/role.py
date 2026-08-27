"""角色 schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RoleBase(BaseModel):
    name: str
    code: str
    remark: str | None = None


class RoleCreate(RoleBase):
    pass


class RoleUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    remark: str | None = None


class RoleOut(RoleBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
