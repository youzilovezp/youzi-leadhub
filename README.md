<div align="center">

<img src="frontend/public/youzi-logo.svg" width="110" alt="youzi logo"/>

# 🎯 Youzi Leadhub · WhatsApp 商机获取系统

**为 WhatsApp Business API 产品获客：挖「做海外生意的中国企业」——投 CTWA 类广告、主页挂 wa.me、在招 WA 客服的出海品牌/跨境大卖，销售直接跟进建联**

![License](https://img.shields.io/badge/License-MIT-orange.svg)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)
![Vue](https://img.shields.io/badge/Vue-3.4-42b883.svg)
![Naive UI](https://img.shields.io/badge/Naive%20UI-2.6-green.svg)

[📖 业务逻辑](docs/业务逻辑.md) · [🚀 脚手架使用说明](项目说明.md) · [🤖 AI 开发手册](AGENTS.md)

</div>

---

## 🚀 30 秒上手

```bash
make dev    # 装依赖 + 准备中间件 + 启动前后端，Ctrl+C 一起停
```

打开 **http://localhost:3000**，账号 `admin` / 见 `backend/.env` 的 `INITIAL_ADMIN_PASSWORD`（默认 `admin`），登录成功 ✅

> 💡 默认密码 `admin/admin` 方便本地开发。**生产前必须改密码：`make admin-pass NEW='<强密码>'`。**

分开跑（输出更清晰）：`make install` → `make backend-dev`（终端 A）→ `make frontend-dev`（终端 B）。

### 🌐 启动后访问

| 服务 | 地址 | 用途 |
|---|---|---|
| 🖥️ 前端 | http://localhost:3000 | 用户界面 |
| ⚡ 后端 API | http://localhost:8000 | REST 接口 |
| 📘 API 文档 | http://localhost:8000/docs | Swagger UI |
| 🗄️ 数据库 UI | http://localhost:8080 | adminer（手动启动：`docker compose --env-file backend/.env up -d adminer`） |

### ⚙️ 可选配置

| 配置项 | 作用 |
|---|---|
| `GOOGLE_MAPS_API_KEY` | google_maps 采集器必需，缺失时任务直接 failed |
| `SCORING_DIM_WEIGHTS` / `TARGET_REGIONS` | 覆盖六维评分权重 / 目标地区 |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | AI 分析/话术/自然语言搜索（OpenAI 兼容：智谱 GLM/DeepSeek 等；未配置降级规则模板） |
| `ENRICH_INTERVAL_HOURS` | C 级线索富化兜底周期（默认 168h；S/A/B 级固定 1/3/7 天更勤） |
| `SCHEDULER_ENABLED=true` | 开启 cron 定时调度（单进程） |

---

## 🤔 这是什么？

给销售找「需要用 WhatsApp 做生意」的企业线索，完整链路：**采集 → 归一化 → 去重合并 → 六维评分分级 → 画像/联系人/事件 → 列表筛选 → 跟进建联**。

```
采集器产出 LeadDraft（meta_ads 主通道 / seed_import 种子导入 / job_posting / website_enrich / 手工录入）
        │
        ▼
归一化：电话 E.164 · 域名 registrable domain · 公司名归一
        │
        ▼
去重合并：三身份列反查，跨来源合并到同一 Lead   ← 系统核心
        │
        ▼
六维评分+分级：出海25% · WhatsApp30% · SaaS需求20% · 规模10% · 营销10% · 联系人5% → S/A/B/C
        │
        ▼
控制台 UI：画像详情（六维/联系人/事件时间线/产品推荐/销售建议）/ 等级筛选 / CSV 导出 / 批量检测 WhatsApp
```

### ✨ 核心能力

| 能力 | 说明 |
|---|---|
| 🔌 多源采集 | 插件式采集器，注册即接入任务/去重/评分体系 |
| 🌱 Seed Pool | CSV 批量导入中国企业种子（`POST /collect/leads/import`，走去重合并，is_cn 标记）——PRD 模块①入口 |
| 📣 `meta_ads` | **主通道**（PRD ICP=中国出海企业）：广告库搜投放企业 → 主页探测 WA/邮箱/官网 + 中文特征 |
| 🗺️ `google_maps` | 辅助源（海外本地商家，非主 ICP）：Places API 按「关键词 × 城市」采集 |
| 💼 `job_posting` | 招聘站点监控（kalibrr 等），在招 WhatsApp 客服的公司即高意向线索 |
| 🔍 `website_enrich` | 富化存量线索：官网检测 WhatsApp/邮箱/社媒 + 场景（客服/营销/交易）与 SaaS 需求关键词，邮箱自动生成联系人 |
| ✍️ 手工录入 | 同样走去重合并 |
| 🧬 去重合并（核心） | 电话 E.164 / 域名 registrable domain / 公司名归一化，三身份列反查跨来源合并 |
| 📊 六维评分分级 | 出海/WhatsApp/SaaS需求/规模/营销/联系人 加权 0-100，S/A/B/C 分级，`SCORING_DIM_WEIGHTS` env 覆盖 |
| 👤 联系人 | 手工 CRUD + 富化邮箱自动生成，职位自动分层（决策层/市场客服/技术），参与评分 |
| 📡 动态事件 | WhatsApp 发现/场景变化/等级迁移等 11 类事件自动记录，详情页时间线 |
| 🎯 产品推荐 | 规则引擎按画像推荐产品 + 销售建议文案（不依赖 LLM） |
| 📤 CSV 导出 | 当前筛选口径、36 个可选字段、UTF-8 BOM（Excel 直接打开） |
| 💼 商机 CRM | 商机漏斗（商机→报价→谈判→成交）联动线索状态，金额/GMV/ARPU/排行榜 |
| 📋 话术审核队列 | AI/模板生成话术 → 销售审核 → 复制发送 → 标记已发（不自动外发） |
| 🤖 AI 能力 | 企业分析/话术生成/自然语言筛选（OpenAI 兼容协议，未配置降级规则模板） |
| 👑 RBAC + 数据权限 | 5 种子角色 × 7 权限码；公司/团队/个人三级数据权限 |
| 📊 销售驾驶舱 | 月度指标、销售漏斗、排行榜、数据源管理（渠道×等级产出） |
| ⏱️ 任务体系 | DB 即队列（无 Celery/Redis），支持并发闸门、取消、进度/日志实时轮询、APScheduler cron 定时 |
| 🖥️ 控制台 UI | 线索筛选（等级/国家/行业/来源/分数/WhatsApp 检测/关键词）、企业画像详情页、勾选批量检测、任务表单按 param_schema 动态渲染、日志流式查看 |

> 📖 业务细节（数据流、去重算法、评分权重、设计取舍）见 [docs/业务逻辑.md](docs/业务逻辑.md)

---

## 📁 项目结构

```
youzi-leadhub/
├── backend/                # Python 后端（FastAPI）
│   ├── app/
│   │   ├── api/v1/endpoints/  # REST 接口
│   │   ├── collectors/        # 插件式采集器（google_maps / job_posting / website_enrich）
│   │   ├── crud/              # 数据访问（lead.py 的 upsert_lead = 去重合并核心）
│   │   ├── models/  schemas/  # SQLAlchemy 模型 / Pydantic 校验
│   │   └── services/          # task_runner（DB 队列）、scheduler（cron）
│   ├── alembic/            # 数据库迁移
│   ├── scripts/            # add_module.py / reset_admin.py
│   └── docs/               # 架构/配置/开发/API 文档
├── frontend/               # Vue 3 + TS
│   └── src/views/collect/  # 线索列表 / 任务列表 / 任务详情
├── docs/业务逻辑.md         # 业务逻辑梳理（数据流/去重/评分/设计取舍）
├── docs/运维部署.md         # 生产部署手册（上线 checklist / 备份恢复 / 故障 runbook / PII 合规）
├── docker-compose.yml      # PostgreSQL / Redis + adminer
├── Makefile                # 常用命令
├── AGENTS.md               # AI 开发手册（模块注册流程、勿动文件清单）
└── 项目说明.md              # 脚手架使用说明
```

---

## 🧩 扩展：新增采集器（三步）

1. `backend/app/collectors/` 写类继承 `Collector`，实现 `run(ctx)`，产出 `LeadDraft` 并 `ctx.emit()`
2. `collectors/__init__.py` 的 `_REGISTRY` 加一行注册
3. 定义 `param_schema`（前端创建任务表单自动渲染）

去重、评分、任务调度、日志、前端表单**全部自动接入**。

---

## 🛠️ 常用命令

```bash
make help            # 查看完整命令列表
make dev             # 一键启动
make test            # 跑后端 + 前端测试（独立临时测试库）
make db-migrate MSG="add order"   # 生成数据库迁移
make db-upgrade      # 应用迁移
make backup          # 备份数据库 → backups/
make restore FILE=backups/app_xxx.sql   # 恢复
make use-sqlite      # 切换 SQLite（零依赖单文件）
make use-pg          # 切换回 PostgreSQL
make admin-pass NEW=xxx  # 重置 admin 密码
```

---

## 🔧 技术栈一览

| 层 | 技术 |
|---|---|
| ⚡ 后端 | FastAPI + SQLAlchemy 2 + Alembic，PostgreSQL（默认）/ SQLite（零依赖模式） |
| 🕷️ 采集 | Crawlee（HTTP 爬虫）、httpx[socks]、phonenumbers、tldextract、APScheduler 3.x |
| 🎨 前端 | Vue 3 + TypeScript + Vite + Naive UI + Tailwind（原子类布局） |
| 🗄️ 中间件 | Docker Compose（PostgreSQL / Redis / adminer），优先复用本机已运行实例 |

---

## 📚 文档导航

| 你想了解什么 | 看这里 |
|---|---|
| 🧠 业务逻辑（数据流 / 去重合并 / 评分 / 任务生命周期 / 设计取舍） | [docs/业务逻辑.md](docs/业务逻辑.md) |
| 🛠️ 运维部署（部署架构 / 上线 checklist / 备份恢复 / 故障 runbook / PII 合规） | [docs/运维部署.md](docs/运维部署.md) |
| 🚀 脚手架使用（启动、命令、加业务模块） | [项目说明.md](项目说明.md) |
| 🤖 AI 助手开发约定 | [AGENTS.md](AGENTS.md) |
| ⚙️ 后端架构 / 配置 / 开发 / API | [架构](backend/docs/架构说明.md) · [配置](backend/docs/配置说明.md) · [开发](backend/docs/开发指南.md) · [API](backend/docs/API文档.md) |
| 🎨 前端架构 / 配置 / 开发 | [架构](frontend/docs/架构说明.md) · [配置](frontend/docs/配置说明.md) · [开发](frontend/docs/开发指南.md) |

---

## 📄 License

MIT
