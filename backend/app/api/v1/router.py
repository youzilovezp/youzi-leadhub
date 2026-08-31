"""API v1 路由聚合。"""

from fastapi import APIRouter

from app.api.v1.endpoints import auth, collect, quality, roles, sales, users

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["认证"])
api_router.include_router(users.router, prefix="/users", tags=["用户管理"])
api_router.include_router(roles.router, prefix="/roles", tags=["角色管理"])
api_router.include_router(collect.router, prefix="/collect", tags=["线索采集"])
api_router.include_router(sales.router, prefix="/sales", tags=["销售工作台"])
api_router.include_router(quality.router, prefix="/quality", tags=["质量抽检"])
