"""角色 CRUD。"""

from app.crud.base import CRUDBase
from app.models.role import Role
from app.schemas.role import RoleCreate, RoleUpdate

role_crud = CRUDBase[Role, RoleCreate, RoleUpdate](Role)
