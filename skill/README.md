<div align="center">

# 🎯 leadhub · 线索采集 Skill

**`/yz:leadhub` → 对话式操控线索采集服务：采集 → 去重合并 → 意向评分 → 筛选导出 CSV**

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![Claude Code](https://img.shields.io/badge/Claude%20Code-Skill-blueviolet.svg)
![opencode](https://img.shields.io/badge/opencode-Skill-green.svg)
![codex](https://img.shields.io/badge/codex-Skill-orange.svg)
![easycode](https://img.shields.io/badge/easycode-Skill-teal.svg)

[📦 安装说明](安装说明.md) · [📖 使用手册](使用手册.md) · [⚙️ SKILL](SKILL.md) · [📚 业务逻辑](../docs/业务逻辑.md)

</div>

---

## 🚀 30 秒上手

```bash
# 1. 装 skill（一次性，4 个 AI 工具全装）
cd <本工程目录>/skill && ./install.sh

# 2. 重开 Claude Code / opencode / codex / easycode，输入:
/yz:leadhub 采集一批吉隆坡牙科诊所的线索
#    （opencode 等不支持冒号的宿主用 /yz-leadhub，功能相同）

# 3. AI 自动：启动服务 → 建任务 → 盯进度 → 报结果
```

线索长这样：

```
#12   95分 [WA 在招] Amare Group Inc.              PH +63917...
#7    50分 [在招]    Get Hooked 360, Inc.          PH —
#5    50分 [-    ]   Primal Enterprises Corporation PH —
```

---

## 🤔 这是什么？

**一个装在 AI 编程助手里、操控本地线索采集服务的 skill**：为销售找「需要用 WhatsApp 做生意」的企业。

```
你说：/yz:leadhub 采集线索
        │
        ▼
🕷️  采集 → google_maps（Maps 商家）· job_posting（在招 WhatsApp 岗位的公司）· website_enrich（官网 WhatsApp 指纹）
        │
        ▼
🧠  加工 → 电话/域名/公司名归一化 → 三身份列去重合并（同企业多来源并 1 条）→ 7 信号评分（满分 110）
        │
        ▼
📊  输出 → 对话内筛选（国家/行业/来源/评分/WhatsApp）→ CSV 导出给销售
```

底层是完整工程（FastAPI + Vue3 + PostgreSQL + Crawlee），skill 通过 CLI 封装其 API——Web 界面与对话操作共存。

---

## ✨ 核心能力

| | 能力 | 说明 |
|---|---|---|
| 🕷️ | **3 采集器** | Maps 商家（官方 API）· 招聘监控（Crawlee）· 网站富化（WhatsApp/邮箱/社媒指纹） |
| 🧠 | **跨来源去重** | 域名 > E.164 电话 > 名称+城市 三身份列反查，同企业多来源自动合并 |
| 📈 | **意向评分** | 7 布尔信号加权（满分 110），权重 `.env` 可覆盖 |
| ⏰ | **定时调度** | 任务配 cron，存 DB 重启自动恢复，闸门满自动排队 |
| 🔍 | **6 维筛选** | 国家 / 行业 / 来源 / 最低分 / WhatsApp 检测 / 关键词 |
| 📤 | **CSV 导出** | 全字段导出，直接喂 CRM / 销售 |
| 🖥️ | **双操作面** | Web 管理台（Vue3）+ 对话式 CLI，同一份数据 |
| 🛠️ | **零依赖 CLI** | skill 执行层 stdlib-only，无视系统代理 |

---

## 📚 文档导航

| 你想了解什么 | 看这里 |
|---|---|
| 🆕 第一次安装 | [📦 安装说明.md](安装说明.md) |
| 🚀 5 分钟跑通 | [📖 使用手册 § 1-3](使用手册.md) |
| 🔧 完整命令 + 参数 | [📖 使用手册 § 4](使用手册.md) |
| ❌ 出错了 | [📖 使用手册 § 6 FAQ + 排错](使用手册.md) |
| 🧠 业务逻辑深挖 | [docs/业务逻辑.md](../docs/业务逻辑.md) |

---

## 🛠️ 技术栈一览

| | 技术 | 用途 |
|---|---|---|
| 🐍 | Python 3.11 + FastAPI | 后端 API + 采集器框架 |
| 🕷️ | Crawlee + httpx + phonenumbers + tldextract | 爬虫 / 富化 / 归一化 |
| ⏰ | APScheduler | cron 定时调度 |
| 💚 | PostgreSQL（Docker，本机已跑则复用） | 存储 |
| 🎨 | Vue 3 + Naive UI | Web 管理台 |
| 🤖 | SKILL.md + stdlib CLI | AI 工具集成层 |

---

## 📄 License

MIT
