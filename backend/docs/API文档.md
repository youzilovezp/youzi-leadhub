# Leadhub 后端 API 文档

> 完整 API 定义在 http://localhost:8000/docs（Swagger UI）—— 本文档只列**核心接口**。

## 1. 接口总览

| 模块 | 路径前缀 | 鉴权 | 说明 |
|---|---|---|---|
| 健康检查 | `/healthz` / `/readyz` | ❌ | `/healthz` 进程级;`/readyz` 含 DB ping |
| 登录/登出 | `/api/v1/auth` | 部分 | `/login` 不需要 token |
| 用户管理 | `/api/v1/users` | ✅ 管理员 | CRUD + 改密码 |
| 角色管理 | `/api/v1/roles` | ✅ 管理员 | CRUD |

## 2. 统一响应格式

成功：

```json
{
  "code": 0,
  "message": "ok",
  "data": { ... }
}
```

失败：

```json
{
  "code": 40100,
  "message": "认证失败",
  "data": null
}
```

> 业务错误通过 HTTP 状态码 + `code` 字段双重表达：
> 401 = 未认证、403 = 权限不足、404 = 资源不存在、422 = 参数校验失败、500 = 服务器错误。

## 3. 业务码

| 业务码 | 含义 |
|---|---|
| `0` | 成功 |
| `40001` | 用户名已存在 |
| `40100` | 认证失败 |
| `40300` | 权限不足 |
| `40400` | 资源不存在 |
| `42200` | 参数校验失败 |
| `50000` | 服务器内部错误 |

## 4. 鉴权

除 `/healthz`、`/readyz`、`/api/v1/auth/login` 外，所有接口都需要：

```
Authorization: Bearer <access_token>
```

token 有效期：60 分钟（`JWT_EXPIRE_MINUTES=60`，可在 .env 调整；含 jti 用于 logout 撤销）。

## 5. 核心接口示例

### 登录

```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "username": "admin",
  "password": "<启动时控制台打印的密码>"
}
```

响应：

```json
{
  "code": 0,
  "data": {
    "access_token": "eyJhbGc...",
    "token_type": "bearer",
    "expires_in": 3600,
    "user": {
      "id": 1,
      "username": "admin",
      "is_superuser": true,
      ...
    }
  }
}
```

### 当前用户

```http
GET /api/v1/auth/me
Authorization: Bearer <token>
```

### 用户列表（分页）

```http
GET /api/v1/users?page=1&page_size=20&username=admin
Authorization: Bearer <token>
```

### 创建用户

```http
POST /api/v1/users
Authorization: Bearer <token>
Content-Type: application/json

{
  "username": "zhangsan",
  "email": "zhangsan@example.com",
  "password": "P@ssw0rd!",
  "nickname": "张三",
  "role_id": 1
}
```

### 修改密码（admin 给别人改）

```http
POST /api/v1/users/{user_id}/password
Authorization: Bearer <token>
Content-Type: application/json

{
  "new_password": "<新密码>"
}
```

> admin 改密**不**需要传 `old_password`，需登录态 + 管理员权限。

## 6. Swagger UI

http://localhost:8000/docs —— 直接点 "Authorize" 输入 token 调试所有接口。

http://localhost:8000/api/v1/openapi.json —— 导出 OpenAPI JSON，可在 Postman 通过 `Import → Import as API` 导入。
