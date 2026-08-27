# AGENTS.md — AI 开发手册（Claude Code / opencode 都读这个）

> 这是给 AI 助手的项目开发约定。人类开发者也建议读一遍。

## 项目结构归属（AI 勿动 / 可改边界）

**框架文件（无特殊需求不要修改）：**
- `backend/app/core/` — 配置/安全/异常/限流/日志
- `backend/app/db/` — 连接/初始化/基类
- `backend/app/api/deps.py`、`backend/app/main.py`、`backend/app/api/v1/router.py`（router.py 只按注册约定追加行）
- `backend/app/crud/base.py`、`backend/app/models/base_class.py`
- `alembic/`（只新增迁移文件，不改旧的）
- `Makefile`、`docker-compose.yml`、`.env.example`

**业务文件（自由修改）：**
- `backend/app/models/<模块>.py`、`backend/app/schemas/<模块>.py`、`backend/app/crud/<模块>.py`、`backend/app/api/v1/endpoints/<模块>.py`
- `frontend/src/views/**`、`frontend/src/api/**`
- `frontend/src/router/index.ts`（追加路由）、`frontend/src/layouts/BasicLayout.vue` 的 `menus` 数组（追加菜单）

## 新增业务模块（标准流程）

```bash
python3 backend/scripts/add_module.py <name> --title "中文标题" --fields "a:str,b:int:0"
```

生成 6 文件后**必须手动注册 5 处**：

1. `backend/app/models/__init__.py`：`from app.models.<name> import <Pascal>`
2. `backend/app/api/v1/router.py`：
   ```python
   from app.api.v1.endpoints.<name> import router as <name>_router
   api_router.include_router(<name>_router, prefix='/<name>', tags=['标题'])
   ```
3. `frontend/src/router/index.ts`：children 里加路由记录
4. `frontend/src/layouts/BasicLayout.vue`：`const menus = [` 数组加 `{ label: '标题', key: '/<name>', icon: renderIcon(DocumentTextOutline) }`（顶部 `import { DocumentTextOutline } from '@vicons/ionicons5'`，经 renderIcon 包裹）
5. 数据库迁移：
   ```bash
   make db-migrate MSG="add <name>"
   make db-upgrade
   ```

## 修改字段（标准流程）

> ⚠️ `add_module.py` 只能**新建**模块，不能改已有模块——改字段按下述流程手改（或让 AI 改）。

1. 改 `backend/app/models/<模块>.py`（加/改列）
2. 同步改三处前端：`src/api/<模块>.ts` 的 interface、`src/views/<模块>/index.vue` 的**表格列**（`n-data-table` 的 columns render）和**表单字段**（`n-modal` 里的 `n-form-item` + `form` 初始值对象）
3. `make db-migrate MSG="change <字段>" && make db-upgrade`
   - ⚠️ SQLite 对 drop column / 改类型支持有限：autogenerate 出来跑不过时，备份数据后删 `backend/data/app.db` 重建（仅开发库）
   - ⚠️ PG 生产库： destructive 迁移前必须 `make backup`

## 高频约定

- 所有 API 返回 `ResponseModel`（`{code, message, data}`）；分页用 `PageResponse`（items/total/page/page_size）
- 写接口全部要求 `SuperUser` 依赖（新模块默认私有，开放权限是业务决策）
- 前端请求走 `@/api/request`（自动带 token、401 统一弹窗）；不要手写 fetch
- 数据库会话用 `SessionDep` 注入，不要自建连接
- 密码只存 bcrypt hash（`hash_password`）；新表记得加 `TimestampMixin`
- 字段校验放 Pydantic schema（min_length/max_length），不要在 endpoint 里手写 if

## 常用命令

```bash
make dev        # 一键启动（装依赖+中间件+前后端）
make test       # 跑测试（临时 sqlite 测试库，不碰开发数据、不需要中间件）
make backup     # 备份 → backups/（带时间戳）
make restore FILE=backups/app_xxx.sql   # 恢复
make db-migrate MSG="..." && make db-upgrade   # 改表结构
make admin-pass NEW='<强密码>'          # 改 admin 密码
make use-sqlite / make use-pg          # 切数据库模式
```

## 中间件策略

- 默认 PostgreSQL：`make start` **优先复用本机已运行的**（探测端口），缺的才 Docker 起
- 复用本机 PG 报"认证失败"→ 按日志指引：首选改 `.env` 的 `POSTGRES_PORT` 走 Docker 独立实例
- 想零依赖：`make use-sqlite`

## 端口策略（动态避让）

- 后端 `PORT`（默认 8000）/ 前端 `FRONTEND_PORT`（默认 3000，读 `backend/.env`）被占时**自动换下一个空闲端口**（往后最多探测 50 个），不报错退出
- 前端端口由 `frontend/vite.config.ts` 的 `pickFreePort` 探测（Makefile 只传 `PORT` 环境变量，**不要加 `--port`**——CLI 参数会覆盖动态探测）；后端由 `scripts/pick_free_port.py` 探测
- `make dev` 下后端换了端口，前端代理（`VITE_PROXY_TARGET`）自动跟随实际端口；单独 `make backend-dev` 换端口时，前端需手动带 `VITE_PROXY_TARGET=http://localhost:<实际端口>`
- 以前端终端实际输出的地址为准（换端口时会有 ⚠️ 提示）

## 出错排查顺序

1. 看后端终端日志（数据库/Redis 问题都有中文诊断和解决命令）
2. `make test` 是否绿（排除环境问题）
3. 前端问题看浏览器 DevTools Console/Network
4. 数据问题：adminer（`docker compose --env-file backend/.env up -d adminer` → http://localhost:8080）

## 前端 UI 生态硬约束（与 youzi-init-project 脚手架一致）

- 组件库唯一 `naive-ui`（unplugin-vue-components 自动按需导入），禁止 Element Plus / Ant Design Vue 等任何其他组件库
- 图标唯一 `@vicons`（xicons 生态）+ `<n-icon>` / `renderIcon`，禁止其他图标库
- 主题走 `n-config-provider` `themeOverrides` + `--yz-*` CSS 设计令牌
- Tailwind 仅作布局/间距原子类；入口必须在 `main.ts` 直连 `import './styles/tailwind.css'`（经 SCSS `@import` 内联会失效）
- 确认弹窗用 `confirm()`（`@/utils/feedback`），提示用 `message.success/error/warning/info`
