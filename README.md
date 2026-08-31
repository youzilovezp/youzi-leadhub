<div align="center">

<img src="frontend/public/youzi-logo.svg" width="110" alt="youzi logo"/>

# 🎯 Youzi Leadhub · WhatsApp 商机获取系统

**为 WhatsApp Business API 产品获客：挖「做海外生意的中国企业」——投 CTWA 类广告、主页挂 wa.me、在招 WA 客服的出海品牌/跨境大卖，销售直接跟进建联**

> 🆓 **纯开源免费运行**：全链路零 API 费用——Meta Ad Library API（免费公开数据）+ DuckDuckGo/SearxNG（零 key/自托管）+ 自研官网爬虫 + 规则引擎评分（LLM 可选，接本地 Ollama 即零成本）。技术栈 FastAPI/Vue3/PostgreSQL 全开源。

![License](https://img.shields.io/badge/License-MIT-orange.svg)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)
![Vue](https://img.shields.io/badge/Vue-3.4-42b883.svg)
![Naive UI](https://img.shields.io/badge/Naive%20UI-2.6-green.svg)

[📘 使用手册](docs/使用手册.md) · [📖 业务逻辑](docs/业务逻辑.md) · [🛠️ 运维部署](docs/运维部署.md) · [🤖 AI 开发手册](AGENTS.md)

</div>

---

## 🚀 30 秒上手

```bash
make dev    # 装依赖 + 准备中间件 + 启动前后端，Ctrl+C 一起停
```

打开 **http://localhost:3000**，账号 `admin` / 密码见 `backend/.env` 的 `INITIAL_ADMIN_PASSWORD`（默认 `admin`），登录成功 ✅

> 💡 默认密码方便本地开发。**生产前必须改密码：`make admin-pass NEW='<强密码>'`。**

分开跑（输出更清晰）：`make install` → `make backend-dev`（终端 A）→ `make frontend-dev`（终端 B）。

### 🌐 启动后访问

| 服务 | 地址 | 用途 |
|---|---|---|
| 🖥️ 前端 | http://localhost:3000 | 用户界面 |
| ⚡ 后端 API | http://localhost:8000 | REST 接口 |
| 📘 API 文档 | http://localhost:8000/docs | Swagger UI（点 Authorize 输 token 调试） |
| 🗄️ 数据库 UI | http://localhost:8080 | adminer（手动启动：`docker compose --env-file backend/.env up -d adminer`） |

> 💡 adminer 登录：Docker 起的 PG 填 Server=`youzi-leadhub-postgres`；复用本机 PG 填 `host.docker.internal`。用户名/密码/库见 `backend/.env`。

### ⚙️ 高频可选配置

| 配置项 | 作用 |
|---|---|
| `META_ADS_ACCESS_TOKEN` | 主通道 meta_ads 必填（免费申请：[Ad Library API](https://www.facebook.com/ads/archive/api)） |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | AI 分析/话术（OpenAI 兼容；可指向本地 Ollama 零成本；未配置降级规则模板，全功能可用） |
| `SEARCH_ENGINE` | `duckduckgo`（默认零 key）/ `searxng`（自托管）/ google_cse、bing（付费加速） |
| `SCORING_DIM_WEIGHTS` / `TARGET_REGIONS` / `SCHEDULER_ENABLED` | 六维评分权重 / 目标地区 / cron 定时调度 |

> 完整配置项：[backend/docs/配置说明.md](backend/docs/配置说明.md) ／ 生产核对清单：[docs/运维部署.md](docs/运维部署.md)

---

## 🤔 这是什么？

给销售找「需要用 WhatsApp 做生意」的企业线索，完整链路：**采集 → 归一化 → 去重合并 → ICP 二重门 → 六维评分分级 → 今日商机批次 → 领取 → 跟进建联 → 成交回传**。

```
采集器产出 LeadDraft（meta_ads 主通道 / web_search / seed_import / job_posting / website_enrich / 手工录入）
        │
        ▼
归一化：电话 E.164 · 域名 registrable domain · 公司名归一
        │
        ▼
去重合并：三身份列反查，跨来源合并到同一 Lead   ← 系统核心
        │
        ▼
ICP 二重门（中国企业 + 出海证据才进销售池）+ 六维评分 → S/A/B/C
        │
        ▼
控制台 UI：今日商机 / 画像详情 / 领取跟进 / CSV 导出 / 批量检测 WhatsApp
```

### ✨ 核心能力

| 能力 | 说明 |
|---|---|
| 🔌 多源采集 | 插件式采集器，注册即接入任务/去重/评分体系；**meta_ads 主通道**挖在投海外广告的中国企业 |
| 🧬 去重合并 | 电话 E.164 / 域名 / 公司名三身份列反查，跨来源合并——同一企业永远只有一条 |
| 🚪 ICP 二重门 | 中国企业 + 出海证据才进销售池，非中国企业默认不出现在列表与导出 |
| 📊 六维评分分级 | 出海25 / WhatsApp30 / SaaS需求20 / 规模10 / 营销10 / 联系人5，加权 0-100 → S/A/B/C |
| 🔥 今日商机 | 每天自动汇总新晋 S/A + 新增高分 + 高价值预警，销售领取即跟进 |
| 👤 联系人 | 手工 CRUD + 官网邮箱自动生成，职位自动分层（决策层/市场客服/技术） |
| 🎯 产品推荐 | 规则引擎双产品线（WA 消息 / 出海 SaaS / 广告代理）+ 销售建议文案，不依赖 LLM |
| 🤖 AI 能力 | 企业分析/话术生成（OpenAI 兼容协议，未配置降级规则模板） |
| 📤 CSV 导出 | 当前筛选口径、36 个可选字段、Excel 直接打开 |
| 👑 RBAC + 数据权限 | 5 种子角色 × 7 权限码；公司/团队/个人三级数据权限 |
| ⏱️ 任务体系 | DB 即队列（无 Celery/Redis），并发闸门、取消、进度/日志实时轮询、cron 定时 |

> 📖 业务细节（数据流、去重算法、评分权重、设计取舍）见 [docs/业务逻辑.md](docs/业务逻辑.md)；界面操作见 [docs/使用手册.md](docs/使用手册.md)。

---

## 📁 项目结构

```
youzi-leadhub/
├── backend/                # Python 后端（FastAPI）
│   ├── app/
│   │   ├── api/v1/endpoints/  # REST 接口
│   │   ├── collectors/        # 插件式采集器（meta_ads / job_posting / website_enrich …）
│   │   ├── crud/              # 数据访问（lead.py 的 upsert_lead = 去重合并核心）
│   │   ├── models/  schemas/  # SQLAlchemy 模型 / Pydantic 校验
│   │   └── services/          # task_runner（DB 队列）、scheduler（cron）
│   ├── alembic/            # 数据库迁移
│   ├── scripts/            # add_module.py / reset_admin.py
│   └── docs/               # 架构/配置/开发/API 文档
├── frontend/               # Vue 3 + TS
│   ├── src/views/collect/  # 今日商机 / 线索 / 任务
│   └── docs/               # 前端架构/配置/开发文档
├── docs/                   # 使用手册 / 业务逻辑 / 运维部署
├── docker-compose.yml      # PostgreSQL / Redis + adminer
├── Makefile                # 常用命令（make help 看全量）
└── AGENTS.md               # AI 开发手册（模块注册、勿动文件清单）
```

---

## 🛠️ 常用命令

```bash
make help            # 查看完整命令列表
make dev             # 一键启动
make test            # 跑后端 + 前端测试（独立临时测试库，不碰开发数据）
make db-migrate MSG="add order"   # 生成数据库迁移
make db-upgrade      # 应用迁移（db-downgrade 回滚一步，慎用）
make backup          # 备份数据库 → backups/
make restore FILE=backups/app_xxx.sql   # 恢复（⚠️ 清库导入，破坏性）
make use-sqlite      # 切换 SQLite（零依赖单文件）；make use-pg 切回
make reset-admin     # 忘了密码？重置为 admin
make admin-pass NEW=xxx  # 重置为指定密码
```

---

## 🔧 技术栈一览

| 层 | 技术 |
|---|---|
| ⚡ 后端 | FastAPI + SQLAlchemy 2 + Alembic，PostgreSQL（默认）/ SQLite（零依赖模式） |
| 🕷️ 采集 | Crawlee（HTTP 爬虫）、Playwright（渲染采集）、httpx[socks]、phonenumbers、tldextract、APScheduler 3.x |
| 🎨 前端 | Vue 3 + TypeScript + Vite + Naive UI + Tailwind（原子类布局） |
| 🗄️ 中间件 | Docker Compose（PostgreSQL / Redis / adminer），优先复用本机已运行实例 |

> 版本明细：[backend/docs/技术栈.md](backend/docs/技术栈.md) · [frontend/docs/技术栈.md](frontend/docs/技术栈.md)

---

## 📚 文档导航

| 你想了解什么 | 看这里 |
|---|---|
| 📘 **怎么用**（销售跟进 / 管理员建任务 / 导出 / 权限） | [docs/使用手册.md](docs/使用手册.md) |
| 🧠 业务逻辑（数据流 / 去重合并 / 评分 / 任务生命周期 / 设计取舍） | [docs/业务逻辑.md](docs/业务逻辑.md) |
| 🛠️ 运维部署（部署架构 / 上线 checklist / 备份恢复 / 故障 runbook / PII 合规） | [docs/运维部署.md](docs/运维部署.md) |
| 🤖 改代码（加模块 / 加采集器 / 勿动文件清单 / 开发约定） | [AGENTS.md](AGENTS.md) |
| ⚙️ 后端架构 / 配置 / 开发 / API | [架构](backend/docs/架构说明.md) · [配置](backend/docs/配置说明.md) · [开发](backend/docs/开发指南.md) · [API](backend/docs/API文档.md) |
| 🎨 前端架构 / 配置 / 开发 | [架构](frontend/docs/架构说明.md) · [配置](frontend/docs/配置说明.md) · [开发](frontend/docs/开发指南.md) |

---

## 📄 License

MIT
