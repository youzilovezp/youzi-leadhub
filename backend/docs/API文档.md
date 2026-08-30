# Leadhub 后端 API 文档

> **完整 schema 见运行时 `/docs`（Swagger UI）**——本文是端点清单与权限速查。
> 注意：`APP_ENV=prod` 且 `DEBUG=false` 时后端会**禁用** `/docs`、`/redoc`、`/api/v1/openapi.json`，
> 生产环境要看 schema 请在测试/开发环境打开，或导出 openapi.json 后离线查看。
> 端点定义源码：`app/api/v1/endpoints/`（auth / users / roles / collect / sales），路由聚合见 `app/api/v1/router.py`。

## 1. 接口总览

| 模块 | 路径前缀 | 鉴权 | 说明 |
|---|---|---|---|
| 健康检查 | `/healthz` `/readyz` | ❌ | 进程级 / 含 DB ping（DB 挂时 503） |
| 认证 | `/api/v1/auth` | 部分 | `/login` 无需 token |
| 用户管理 | `/api/v1/users` | ✅ 超管 | CRUD + 重置密码 |
| 角色管理 | `/api/v1/roles` | ✅ 超管 | CRUD（RBAC 权限码） |
| 线索采集 | `/api/v1/collect` | ✅ 登录 | 线索 / 联系人 / 事件 / 分配 / 跟进 / 任务 / 统计 |
| 销售工作台 | `/api/v1/sales` | ✅ 登录 | 商机 / 话术 / 预警 / AI / 漏斗排行 |

## 2. 统一响应格式

成功：

```json
{ "code": 0, "message": "ok", "data": { ... } }
```

失败：

```json
{ "code": 40100, "message": "认证失败", "data": null }
```

> 业务错误通过 HTTP 状态码 + `code` 字段双重表达：
> 401 = 未认证、403 = 权限不足、404 = 资源不存在、422 = 参数校验失败、429 = 限流、500 = 服务器错误。

## 3. 业务码

| 业务码 | 含义 |
|---|---|
| `0` | 成功 |
| `40001` | 业务冲突（用户名/邮箱已存在、未知采集器、参数非法等） |
| `40100` | 认证失败 |
| `40300` | 权限不足 |
| `40400` | 资源不存在 |
| `42200` | 参数校验失败 |
| `42900` | 请求过于频繁（slowapi 限流触发，需 Redis 模式） |
| `50000` | 服务器内部错误 |

## 4. 鉴权与权限模型

### 4.1 Token

除 `/healthz`、`/readyz`、`POST /api/v1/auth/login` 外，所有接口都需要：

```
Authorization: Bearer <access_token>
```

- JWT 有效期 60 分钟（`JWT_EXPIRE_MINUTES`，含 `jti`）；登出（`/auth/logout`）把 jti 写入
  `token_blacklist` 表即刻失效。
- 登录限流（DB 计数，不依赖 Redis）：同 `用户名+IP` 连续 5 次失败锁定、同 IP 20 次失败锁定，
  指数退避封顶 1 小时；客户端 IP 取 `X-Forwarded-For` 首跳（反代必须正确传递，见 docs/运维部署.md §2.4）。

### 4.2 三类访问控制

| 标记 | 含义 |
|---|---|
| **SuperUser** | 超级管理员（`is_superuser=true`） |
| **CurrentUser** | 任意登录用户 |
| **`权限码`**（如 `assign:lead`） | 角色持有该权限码（`roles.permissions`）；超管旁路全量校验 |

权限码词表（`app/models/role.py PERMISSION_CODES`，7 个）：
`lead:read` / `lead:write` / `lead:delete` / `assign:lead` / `task:manage` / `user:manage` / `stats:read`。

种子角色：`admin`（全量）、`sales_manager`（lead:read/write + assign:lead + stats:read）、
`sales`（lead:read/write）、`operator`（lead:read + task:manage + stats:read）、
`data_admin`（lead:read + stats:read）。

> 当前代码实际以权限码校验的端点：`assign:lead`（分配/释放/自动分配）、`lead:read`（自然语言搜索）、
> `stats:read`（漏斗/排行榜/数据源）。任务管控与删线索在代码里挂的是 SuperUser；
> 用户/角色管理挂 SuperUser。

### 4.3 数据权限（重要）

用户 `data_scope` 三级（`app/api/perms.py`）：

| 级别 | 可见范围 |
|---|---|
| `all`（默认，超管强制） | 全部线索 |
| `team` | 本团队成员 + 共享池（`owner_id IS NULL`） |
| `own` | 自己 + 共享池 |

下表「数据权限」列标 ✅ 的端点会做该过滤：列表/导出强拼 SQL 条件（**接口层无旁路**），
详情/操作类对受限范围外的线索**直接 404**（不泄露存在性）。

## 5. 端点清单

> 格式：`方法 路径` ｜ 说明 ｜ 权限要求。

### 5.1 健康检查（无前缀）

| 端点 | 说明 | 权限 |
|---|---|---|
| `GET /healthz` | 进程级存活（liveness） | ❌ 无 |
| `GET /readyz` | 含 DB ping，DB 不可用返回 503（readiness） | ❌ 无 |

### 5.2 认证 `/api/v1/auth`

| 端点 | 说明 | 权限 |
|---|---|---|
| `POST /auth/login` | 登录（JSON 或 form-urlencoded；失败限流见 §4.1） | ❌ 无 |
| `GET /auth/me` | 当前用户信息（含角色、data_scope） | CurrentUser |
| `POST /auth/logout` | 登出（jti 进 token_blacklist 即刻失效） | CurrentUser |

### 5.3 用户管理 `/api/v1/users`

| 端点 | 说明 | 权限 |
|---|---|---|
| `GET /users` | 用户列表（分页，`username` / `is_active` 筛选） | SuperUser |
| `POST /users` | 创建用户（用户名/邮箱查重、role_id 外键校验） | SuperUser |
| `GET /users/{user_id}` | 用户详情 | SuperUser |
| `PUT /users/{user_id}` | 更新用户（nickname/email/phone/avatar/role_id/is_active） | SuperUser |
| `DELETE /users/{user_id}` | 删除用户（不能删自己；不能删最后一个超管） | SuperUser |
| `POST /users/{user_id}/password` | 管理员重置密码（无需旧密码） | SuperUser |

### 5.4 角色管理 `/api/v1/roles`

| 端点 | 说明 | 权限 |
|---|---|---|
| `GET /roles` | 角色列表（含 permissions 权限码数组） | SuperUser |
| `POST /roles` | 创建角色（code 唯一） | SuperUser |
| `GET /roles/{role_id}` | 角色详情 | SuperUser |
| `PUT /roles/{role_id}` | 更新角色（name/remark/permissions） | SuperUser |
| `DELETE /roles/{role_id}` | 删除角色（内置 admin 角色不可删；仍有用户的角色不可删） | SuperUser |

### 5.5 线索 `/api/v1/collect`

| 端点 | 说明 | 权限 |
|---|---|---|
| `GET /collect/leads` | 线索列表（筛选：国家/行业/来源/最低分/`grade` S·A·B·C/WhatsApp 检测/关键词/跟进状态/跟进人/`due_follow` 该回访/是否中国出海；行含 `contacts_count`、`recommended_products`、`owner_name`） | CurrentUser ＋ 数据权限 ✅ |
| `POST /collect/leads` | 手工录入线索（同样走去重合并） | CurrentUser |
| `GET /collect/leads/export` | 导出 CSV（当前筛选口径，`fields` 指定列、`limit`≤50000，UTF-8 BOM 兼容 Excel，流式分批产出） | CurrentUser ＋ 数据权限 ✅ |
| `GET /collect/leads/{lead_id}` | 线索详情（企业画像：六维分+权重、联系人、事件/跟进各 50 条、产品推荐、销售建议、信号证据链、商机） | CurrentUser ＋ 数据权限 ✅（范围外 404） |
| `DELETE /collect/leads/{lead_id}` | 删除线索（级联删联系人/事件/跟进，PG 下信号/话术/商机外键级联） | SuperUser |
| `POST /collect/leads/check-whatsapp` | 勾选线索 → 创建隐式 `website_enrich` 任务（复用进度/取消/闸门） | CurrentUser |
| `GET /collect/collectors` | 采集器列表（含 param_schema，前端动态表单） | CurrentUser |
| `GET /collect/geo-options` | 国家/城市选项（表单联动数据源） | CurrentUser |
| `GET /collect/industries` | 行业选项（库存 distinct + 计数） | CurrentUser |
| `GET /collect/stats` | 采集总览（总数/WhatsApp 数/等级分布/活跃任务/待跟进/该回访/中国出海数/本月新增与成交） | CurrentUser |

### 5.6 联系人 `/api/v1/collect`（挂在线索下）

| 端点 | 说明 | 权限 |
|---|---|---|
| `GET /collect/leads/{lead_id}/contacts` | 联系人列表 | CurrentUser ＋ 数据权限 ✅ |
| `POST /collect/leads/{lead_id}/contacts` | 新增联系人（seniority 按职位自动分层；同邮箱 40001） | CurrentUser ＋ 数据权限 ✅ |
| `PUT /collect/leads/{lead_id}/contacts/{contact_id}` | 编辑联系人（变更触发重评 + 事件） | CurrentUser ＋ 数据权限 ✅ |
| `DELETE /collect/leads/{lead_id}/contacts/{contact_id}` | 删除联系人 | CurrentUser ＋ 数据权限 ✅ |

### 5.7 动态事件 `/api/v1/collect`

| 端点 | 说明 | 权限 |
|---|---|---|
| `GET /collect/leads/{lead_id}/events` | 线索动态事件分页（时间线，11 类事件） | CurrentUser ＋ 数据权限 ✅ |

### 5.8 分配 `/api/v1/collect`

| 端点 | 说明 | 权限 |
|---|---|---|
| `POST /collect/leads/{lead_id}/assign` | 分配/转移跟进人（撞单锁定：分配后其他销售只读） | **`assign:lead`** |
| `POST /collect/leads/{lead_id}/release` | 释放回共享池 | **`assign:lead`** |
| `POST /collect/leads/auto-assign` | 自动分配：共享池线索按当前负载轮转分给候选销售（可按等级/分数/行业/国家过滤，`max_per_owner` 上限） | **`assign:lead`** |

### 5.9 跟进 `/api/v1/collect`

| 端点 | 说明 | 权限 |
|---|---|---|
| `GET /collect/follow-options` | 跟进弹窗选项（状态词表 + 活跃用户下拉） | CurrentUser |
| `POST /collect/leads/{lead_id}/follow-up` | 记录跟进：更新线索跟进人/状态/时间并写一条历史。普通销售只能跟进共享池/自己的线索（撞单锁定）；把线索改派他人需 `assign:lead` | CurrentUser ＋ 数据权限 ✅ |
| `GET /collect/leads/{lead_id}/follow-ups` | 跟进历史（最近 50 条倒序） | CurrentUser ＋ 数据权限 ✅ |

跟进状态 10 态：`unassigned` 未分配 / `pending` 待跟进 / `contacted` 已联系 / `replied` 已回复 /
`opportunity` 有效商机 / `quote` 报价 / `negotiation` 谈判 / `won` 成交 / `invalid` 无效 / `paused` 暂不考虑。

### 5.10 采集任务 `/api/v1/collect`

| 端点 | 说明 | 权限 |
|---|---|---|
| `GET /collect/tasks` | 任务列表（分页，`collector` / `status` 筛选，含 `created_by_name`） | CurrentUser |
| `POST /collect/tasks` | 创建任务（校验采集器与参数；带 `cron_expr` 则注册定时调度，否则立即入队） | SuperUser |
| `GET /collect/tasks/{task_id}` | 任务详情（进度/计数/错误） | CurrentUser |
| `PUT /collect/tasks/{task_id}` | 更新任务（参数增量合并校验；改 cron/enabled 会同步调度） | SuperUser |
| `DELETE /collect/tasks/{task_id}` | 删除任务（running 中先取消；日志一并删） | SuperUser |
| `POST /collect/tasks/{task_id}/run` | 立即执行（入队，受并发闸门排队） | SuperUser |
| `POST /collect/tasks/{task_id}/cancel` | 取消任务（排队中直接 cancelled；运行中置取消事件） | SuperUser |
| `GET /collect/tasks/{task_id}/logs` | 任务日志（`after_id` 增量轮询） | CurrentUser |

> 任务执行依赖**单进程**后端（`WORKERS=1`），多 worker 部署时任务会卡 queued——见 docs/运维部署.md §1.1/§4.1。

### 5.11 商机 `/api/v1/sales`

| 端点 | 说明 | 权限 |
|---|---|---|
| `GET /sales/leads/{lead_id}/opportunities` | 商机列表 | CurrentUser ＋ 数据权限 ✅ |
| `POST /sales/leads/{lead_id}/opportunities` | 新增商机（线索状态联动推进到有效商机） | CurrentUser ＋ 数据权限 ✅ |
| `PUT /sales/leads/{lead_id}/opportunities/{opp_id}` | 推进阶段/改金额（成交联动线索状态 `won`） | CurrentUser ＋ 数据权限 ✅ |
| `DELETE /sales/leads/{lead_id}/opportunities/{opp_id}` | 删除商机 | CurrentUser ＋ 数据权限 ✅ |
| `GET /sales/stage-options` | 商机阶段词表 | CurrentUser |

### 5.12 话术审核队列 `/api/v1/sales`

| 端点 | 说明 | 权限 |
|---|---|---|
| `GET /sales/messages` | 话术队列（`status` / `lead_id` 筛选，分页） | CurrentUser ＋ 数据权限 ✅ |
| `POST /sales/leads/{lead_id}/messages/generate` | 生成首触话术（LLM，未配置自动降级模板）并进入待审核队列 | CurrentUser ＋ 数据权限 ✅ |
| `POST /sales/messages/{message_id}/review` | 审核话术（`approve`/`reject`）或标记已发送（`mark_sent`，人工复制发送后回填；**系统不自动外发**） | CurrentUser ＋ 数据权限 ✅ |

### 5.13 高价值预警 `/api/v1/sales`

| 端点 | 说明 | 权限 |
|---|---|---|
| `GET /sales/alerts` | 高价值客户预警（发现 WhatsApp / SaaS 信号 / 等级升 S·A 等标记 `is_alert` 的事件，分页） | CurrentUser ＋ 数据权限 ✅ |

### 5.14 AI 能力 `/api/v1/sales`

| 端点 | 说明 | 权限 |
|---|---|---|
| `GET /sales/leads/{lead_id}/ai-analysis` | AI 分析客户（企业概况/机会/痛点/推荐/切入点；LLM 未配置降级规则模板，`generated_by` 标记来源） | CurrentUser ＋ 数据权限 ✅ |
| `POST /sales/leads/{lead_id}/sales-script` | 生成销售话术（不落库，预览用；入队列走 messages/generate） | CurrentUser ＋ 数据权限 ✅ |
| `POST /sales/leads/search-nl` | 自然语言 → 结构化筛选参数（**需配置 LLM**，未配置返回 40001 提示） | **`lead:read`** |

### 5.15 统计（漏斗/排行/数据源） `/api/v1/sales`

| 端点 | 说明 | 权限 |
|---|---|---|
| `GET /sales/funnel` | 销售漏斗（各阶段线索数 + 商机金额口径） | **`stats:read`** |
| `GET /sales/leaderboard` | 销售排行榜（线索数/商机数/成交数/成交金额） | **`stats:read`** |
| `GET /sales/data-sources` | 数据源管理（per-collector 任务数/成功率/数据量/最后运行 + 渠道×等级产出） | **`stats:read`** |

## 6. 调用示例

### 登录

```http
POST /api/v1/auth/login
Content-Type: application/json

{ "username": "admin", "password": "<INITIAL_ADMIN_PASSWORD>" }
```

响应：

```json
{
  "code": 0,
  "data": {
    "access_token": "eyJhbGc...",
    "token_type": "bearer",
    "expires_in": 3600,
    "user": { "id": 1, "username": "admin", "is_superuser": true }
  }
}
```

### 线索列表（筛选 + 分页）

```http
GET /api/v1/collect/leads?grade=S&whatsapp_hit=true&due_follow=true&page=1&page_size=20
Authorization: Bearer <token>
```

### 导出 CSV

```http
GET /api/v1/collect/leads/export?grade=A&fields=name,country,phone_e164,contacts_summary&limit=5000
Authorization: Bearer <token>
```

### 分配线索给销售（需 assign:lead 权限码）

```http
POST /api/v1/collect/leads/42/assign
Authorization: Bearer <token>
Content-Type: application/json

{ "owner_id": 3 }
```

## 7. Swagger UI

- 开发环境：http://localhost:8000/docs —— 点 "Authorize" 输入 token 调试所有接口。
- OpenAPI JSON：http://localhost:8000/api/v1/openapi.json（可导入 Postman）。
- 生产环境（`APP_ENV=prod`）以上两个入口均禁用。
