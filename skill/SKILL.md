---
name: yz:leadhub
description: 线索采集服务控制台。为销售获取「需要用 WhatsApp 做生意」的企业商机线索：采集（Google Maps 商家/招聘监控/网站富化）→ 去重合并 → 意向评分（满分110）→ 筛选导出。当用户想采集商机线索、监控在招 WhatsApp 客服的公司、批量检测企业官网 WhatsApp、查/筛/导出线索库时触发。触发短语：「/yz:leadhub」「/yz-leadhub」「采集线索」「跑一个采集任务」「检测 WhatsApp」「线索列表」「导出线索」「leadhub 状态」。
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# /yz:leadhub · 线索采集服务控制台

> 一句话：对话式操控本地 leadhub 服务——建采集任务、盯进度、查高意向线索、一键给销售导 CSV。

## 执行入口

所有操作走一个 CLI（stdlib-only，无需 pip）：

```bash
python3 <skill-root>/scripts/leadhub.py <command> [args]
```

环境变量（可选）：`LEADHUB_URL`（默认 http://127.0.0.1:8000）、`LEADHUB_USER/LEADHUB_PASS`（默认 admin/admin）、`LEADHUB_DIR`（复制安装时指向工程根）。

## 标准工作流

1. **查状态**：`leadhub.py status`。未运行 → `leadhub.py start`（首次约 30-60s 建库建表）。
2. **按用户意图选命令**（见下表），把 CLI 原始输出贴给用户，不要转述丢细节。
3. **长任务**（采集/富化）加 `--wait` 阻塞到终态，或 `task-logs <id> -f` 跟日志。
4. **销售要名单** → `leadhub.py export -o xxx.csv`。

## 命令地图

| 意图 | 命令 |
|---|---|
| 服务状态 / 启动 / 停止 | `status` / `start` / `stop` |
| 创建采集任务并等待 | `task-create <collector> -p k=v... [--cron '0 9 * * *'] --wait` |
| 任务列表 / 详情 / 日志 | `task-list [--status running]` / `task-get <id>` / `task-logs <id> [-f]` |
| 执行 / 取消任务 | `task-run <id> [--wait]` / `task-cancel <id>` |
| 检测 WhatsApp | `check-whatsapp [id...] --wait`（不带 id = 全库「有网站且24h未富化」） |
| 查线索 | `leads [--country MY] [--industry x] [--source x] [--min-score 40] [--whatsapp hit] [--keyword x]` |
| 导出 CSV | `export [-o file.csv]` |

## 三个采集器（task-create 的 collector 与必填参数）

| collector | 参数 | 说明 |
|---|---|---|
| `google_maps` | `-p country=MY -p cities="Kuala Lumpur, Penang" -p keywords="dental clinic"` | Places API；需 backend/.env 配 `GOOGLE_MAPS_API_KEY`；每关键词≤60条 |
| `job_posting` | `-p keywords=whatsapp -p max_pages=3` | kalibrr 招聘监控；岗位→公司级线索合并，`在招 WhatsApp 岗位` +30 分 |
| `website_enrich` | 无参数=全库 eligible；或列表勾选 id 走 `check-whatsapp` | 检测官网 WhatsApp 插件/wa.me、邮箱、社媒；命中 +40 分 |

## 评分速查（满分 110 不封顶）

WhatsApp插件+40 ｜ 在招岗位+30 ｜ 官网+10 ｜ 邮箱+10 ｜ 目标地区(MY/SG/ID/TH/PH/VN/AE/SA/QA/KW/BR/MX/CO/AR/CL)+10 ｜ 电话+5 ｜ 社媒+5

## 示例

```bash
# 采一批吉隆坡牙科诊所并等结果
python3 scripts/leadhub.py task-create google_maps -p country=MY \
  -p cities="Kuala Lumpur" -p keywords="dental clinic" --wait

# 每天早9点自动跑招聘监控
python3 scripts/leadhub.py task-create job_posting -p keywords=whatsapp --cron '0 9 * * *'

# 全库检测 WhatsApp，然后导出 ≥40 分的高意向名单
python3 scripts/leadhub.py check-whatsapp --wait
python3 scripts/leadhub.py leads --min-score 40 --whatsapp hit
python3 scripts/leadhub.py export -o hot-leads.csv
```

## 排错

- `无法连接 … 服务未启动` → `start`；90s 未就绪看 `tail -50 /tmp/leadhub-dev.log`
- google_maps 任务 failed 提示无 KEY → `backend/.env` 配 `GOOGLE_MAPS_API_KEY` 后重跑
- 端口冲突 → 改 `backend/.env` 的 `PORT`/`FRONTEND_PORT`/`POSTGRES_PORT`，CLI 跟着 `LEADHUB_URL` 走
- 业务细节/架构看 `<工程根>/docs/业务逻辑.md`
