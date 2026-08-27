"""登录/登出/当前用户信息。

端点接受两种 content-type：
- application/json          —— 前端 axios 默认走这个
- application/x-www-form-urlencoded —— Swagger UI Authorize 用

rate limit / blacklist / audit 都在 auth_service.login 与 logout 中实现。
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.api.deps import CurrentUser, SessionDep
from app.core.exceptions import AuthError
from app.schemas.common import ResponseModel
from app.schemas.user import LoginRequest, LoginResponse, UserOut
from app.services.auth_service import auth_service

router = APIRouter()


class LoginJSON(BaseModel):
    """JSON 登录（前端 axios 默认走这个）。"""

    username: str
    password: str


async def _read_form(request: Request):
    """读 form-encoded body；bytes 字段转 str。"""
    form = await request.form()
    username = form.get("username")
    password = form.get("password")
    if username is None or password is None:
        raise AuthError("缺少 username 或 password")
    return LoginRequest(
        username=username.decode("utf-8") if isinstance(username, bytes) else username,
        password=password.decode("utf-8") if isinstance(password, bytes) else password,
    )



@router.post("/login", response_model=ResponseModel[LoginResponse], summary="登录")
async def login(db: SessionDep, request: Request):
    """无 Redis 时不做限流；生产建议 ENABLE_REDIS=true 打开限流。"""
    content_type = (request.headers.get("content-type") or "").lower()

    if content_type.split(";")[0].strip() == "application/json":
        try:
            body = await request.json()
            json_body = LoginJSON(**body)
        except Exception as exc:
            # 非法 JSON / body 不是对象 → 422 参数错，而不是 500
            raise AuthError(f"请求体不是合法的 JSON 对象: {type(exc).__name__}") from exc
        payload = LoginRequest(username=json_body.username, password=json_body.password)
    elif (
        "application/x-www-form-urlencoded" in content_type
        or "multipart/form-data" in content_type
    ):
        payload = await _read_form(request)
    else:
        raise AuthError("不支持的 Content-Type")

    data = await auth_service.login(db, payload)
    return ResponseModel(data=data)

@router.get("/me", response_model=ResponseModel[UserOut], summary="当前用户信息")
async def me(user: CurrentUser):
    return ResponseModel(data=UserOut.model_validate(user))


@router.post("/logout", response_model=ResponseModel, summary="登出")
async def logout(user: CurrentUser, request: Request):
    """把当前 token 的 jti 写入 Redis 黑名单（剩余期内 401）。

    无 Redis 或写失败时仍返回成功——本地 state 已清，但服务端 token 撤销未生效。
    生产必须 ENABLE_REDIS=true。
    """
    from app.core.security import blacklist_token
    token = request.headers.get("authorization", "")
    if token.lower().startswith("bearer "):
        ok = await blacklist_token(token.split(" ", 1)[1])
        msg = "已退出（token 已撤销）" if ok else "已退出（token撤销未生效，请联系管理员）"
    else:
        msg = "已退出"
    return ResponseModel(message=msg)
