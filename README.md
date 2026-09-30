# youzi-LeadHub — WhatsApp BSP 线索获取管道

**youzi-LeadHub · 线索面板**——WhatsApp BSP（Business Solution Provider）销售线索中心：发现全球用 WhatsApp 承接客户的海外企业线索、P0 分层打分、合规留痕。

依据 [`docs/whatsapp-bsp-leadgen-可行性分析.md`](docs/whatsapp-bsp-leadgen-可行性分析.md)（**v7 实测校准版**）实施。
获取"在用 WhatsApp 承接客户的海外企业"线索：**不限中国出海（P0 优先层），全量入库分层打分**。

> ⚠️ **合规红线（报告第七节）**：本工具只做**线索发现与准备**，不包含任何外联/发送能力。
> 绝不对爬到的号码做 WhatsApp 群发/自动化营销；不引入反爬绕过层，被挑战即弃站。

## v7 实测校准基线（不要再用 v5/v6 的旧数字）

| 渠道 | v7 实测命中率 | 状态 |
|------|--------------|------|
| **App 渠道（Play）** | **12.5%**（6038 站 / 1122 号码） | **主力** |
| Shopify 生态（CDX） | 0.7%（55% 死站） | 淘汰（CDX 档期种子大量过期试用店） |
| Tranco top-N | 1.0%（1255 站） | 淘汰（头部大站走表单/SaaS 客服，WA 长尾不在头部） |
| iTunes RSS / 本地目录 | 未测 | 暂缓（play 单渠道已超验收线） |
| Common Crawl WAT | $25–50/次 + 头部偏置 | **回填**（最后走，D6+ 触发） |

> 验收线：单渠道 ≥500 条带号码线索 → play 渠道实测 771 域达成（154%）；
> 链接层 precision ≥98% + ≥100 条中国出海 P0 → ⏳待 `docs/golden-set-sample.csv` 人工标注复核。

## 技术选型（GitHub 视角全新评估，不重复造轮子）

| 层 | 选型 | 理由 |
|----|------|------|
| 爬取调度 | **Scrapy** | robots 遵守 / 自动限速 / 重试（含 Retry-After）/ 去重（剥 utm）/ JOBDIR / 2MB 截断 / 8s 超时 全内置 |
| HTML 解析 | **parsel**（Scrapy 自带） | 链接提取用成熟解析器 |
| 号码归一 | **phonenumbers** | Google libphonenumber 官方移植，E164 + 区域码 |
| 域名归一 | **tldextract**（PSL 私有段） | `myshopify.com` 等平台后缀回退 host 粒度 |
| 种子下载 | **httpx** | Tranco zip / Common Crawl CDX REST |
| App 种子 | **google-play-scraper**（JS 版） | 按国家×类目分榜；输出 `URL<TAB>developer` 作为 P0 第 1 强信号 |
| API | **FastAPI + uvicorn** | 只读 `/api/stats`、`/api/leads`（支持 market_group 过滤） |
| 前端 | React 19 + Vite + Tailwind v4 | 用户指定 UI 库 |
| 测试 | **pytest**（**73 项**） | 涵盖 detect / normalize / pipeline / spider / middleware / osm / dupefilter |

> **从 ai-radar 切换到 Scrapy 的偏离**：Spec 原计划复用 ai-radar 引擎（剥离绕过层）。
> 实测后改 Scrapy——本场景 6 项工程节流 Scrapy 全内置对应（C1 标），Clawl4AI 默认浏览器渲染
> 违反 no-browser 红线，httpx + 自研调度性价比低。**判定：可接受，无需替换**。

## 快速开始

```bash
cd youzi-bsp
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/        # 73 项全绿
```

## 跨机器部署与数据迁移（2026-09-30）

**语义：代码走 git，数据走快照。** 工程不含任何本机绑定——play 渠道代理自动解析
（`BSP_PROXY` 显式指定 > 本机 7890 探测 > 直连，海外 VPS 直连即可）。

```bash
# 旧机器：打包（SQLite 一致性快照，API 运行中执行也安全）
python -m youzi_bsp backup
# → data/backup-<时间戳>.tar.gz（线索库 + 种子池 + JOBDIR 增量进度 + 金标准标注）

# 新机器：复活全部状态
git clone <repo> && cd youzi-leadhub
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python -m youzi_bsp restore --from backup-<时间戳>.tar.gz   # 先停 API/爬取进程
.venv/bin/uvicorn youzi_bsp.api:app --host 0.0.0.0 --port 8788   # 必须从仓库根目录启动
```

恢复后增量爬取语义连续（JOBDIR 的 requests.seen 一起迁移，不会全量重爬）。
前端：`cd frontend && pnpm i && pnpm build`（8788 托管 dist）或 `pnpm dev`（5173 开发态）。

⚠ 注意：恢复是覆盖式；`docs/` 在 .gitignore 里（金标准 CSV 只活在快照与备份中）。

## 端到端管道（D1–3 完成 / D6+ 待触发）

```bash
# 1) 生成种子
.venv/bin/python -m youzi_bsp seed tranco --top 2000
.venv/bin/python -m youzi_bsp seed myshopify --limit 2000
.venv/bin/python -m youzi_bsp seed play --country id --category BUSINESS --limit 200   # 输出带 developer_name
.venv/bin/python -m youzi_bsp seed osm --country MY,TH,PH,ID --limit 500
.venv/bin/python -m youzi_bsp seed sample --file your-list.txt

# 2) 分渠道爬取（礼貌：robots / 自动限速 / 每站 ≤5 页 / 2MB 截断 / 8s 超时）
.venv/bin/python -m youzi_bsp crawl --seed-file data/seeds-play.txt --channel play --limit 6038
# 断点续跑：--jobdir 缺省时落到 data/.job/<channel>-<yyyymmdd-HHMMSS>/

# 3) 命中率统计 + CSV 导出（market_group / developer_name / widget / email 都进 CSV）
.venv/bin/python -m youzi_bsp stats
.venv/bin/python -m youzi_bsp export --out data/leads.csv

# 4) GDPR 删除响应（合规 §7.4 必选）
.venv/bin/python -m youzi_bsp forget --entity example.com
```

## 数据模型（SQLite，PG 兼容三表 + 迁移列）

```
domain(entity_key, channel, seed_host, developer_name,
       status, widget, p0, market, market_group, lang, score,
       first_seen, last_crawled)
phone(e164, valid, country)
sighting(entity_key, e164, url, layer, first_seen, last_seen)   -- 多对多 + 合规留痕
```

**幂等**：全部 upsert（ON CONFLICT），widget/email OR-merge 用 SQLite 原子 SQL
（`json_each + GROUP_CONCAT`，多进程并发安全）。

## 核心能力

### WhatsApp 检测（两层扫描）

- **链接层**：`wa.me/(\+?\d…)`（含 `%2B` 编码）+ `api/web/wp.whatsapp.com/send/?phone=` + `whatsapp://send` + 9 种 widget 指纹
- **文本层**：关键词"whatsapp" ±100 字符邻域，国际格式（`+`/`00` 开头），libphonenumber 校验
- **widget 指纹**：ht-ctc / joinchat / getbutton / chaty / elfsight / tidio / wp-chat / click-to-chat / whatsapp-chat
- **SaaS widget 静态不可见**（Elfsight/Tidio）只记指纹，不进 candidates

### 三层归一

- **URL**：小写 host / 去 fragment / 剥 UTM/fbclid/gclid/mc_/ref / 统一尾斜杠
- **号码**：URL-decode + 宽松进 + libphonenumber 严格出 + E164
- **实体**：eTLD+1 + 平台后缀（myshopify.com / blogspot.com）回退 host + punycode/IDN

### P0 判定（spec §3.5 + v7 增强）

强信号（任一满足且非 gray 即 P0）：
1. **App 开发者主体含中国公司名**（P0 第 1 强信号；play 渠道独有）
2. ICP 备案号
3. 中国 TLD（.cn / .中国 / .xn--fiqs8s）
4. lang=zh 且号码市场=CN
5. hreflang=zh 且号码市场 ∈ {CN, HK, MO, TW}（双轨升格，避免跨国站过杀）

弱加分（已不再是强信号，避免 github/google/stripe 过杀）：
- hreflang=zh 单独
- lang=zh 单独
- title 中文

### 市场分组（v7 新增）

`{SEA, LATAM, MENA, EU, OTHER}` 标签——spec §3.5 要求。之前只返单一 ISO 国家码，
销售无法按市场分批消化。现已加 `_MARKET_GROUP` 映射（覆盖 60+ 国家），写入 `domain.market_group`，
`/api/leads?market_group=SEA` 可直接按市场过滤。

## D6+ 待办（不在当前实现）

- [ ] Common Crawl WAT 全量回填（$25–50/次）
- [ ] 分级重访调度（周级 P0 / 月级长尾，触发条件：高分池 ≥5000）
- [ ] 特征 diff（号码集合变化 = 新挂线索流）
- [ ] 开场白草稿按语言按需生成

## 检测能力边界（v6.1）

- 两层扫描 + 文本层仅国际格式（误报控制，spec §9.2）
- **SaaS widget 静态不可见**（Elfsight/Tidio）只记指纹；真实覆盖率靠金标准集人工标注复核
- app-only 无官网商家不可达（范围边界，非漏检）
- **建议跑在海外 VPS**：CN 出口会被部分站点降级/拒绝

## 数据状态（v7→v8 迁移）

⚠️ v7 文档声明的 771 域/1122 号码（play 渠道实测基线）来自一次历史跑批，**当前
`data/leads.db` 是后续 OSM 单渠道重跑后的状态**（691 域 / 605 号码 / 1 P0）。

要复现 v7 baseline，重跑：
```bash
.venv/bin/python -m youzi_bsp seed play --country id,br,mx,ph,th,vn \
     --category BUSINESS,SHOPPING,COMMUNICATION --limit 150 --out data/seeds-play.txt
.venv/bin/python -m youzi_bsp crawl --seed-file data/seeds-play.txt \
     --channel play --limit 6038
.venv/bin/python -m youzi_bsp stats
```

## 文档与代码一致性

- 文档版本：v7（实测校准版），引用基线已替换为实测数字
- README 当前版本对齐 v7；后续 v8 迭代应同步更新 README（不再引用 v6.1 / v5.1 的旧数字）

## v7 验收线追踪

| 验收线 | 状态 | 证据 |
|--------|------|------|
| 单渠道 ≥500 条带号码线索 | ✅ | play 渠道历史实测 771 域 / 1122 号码（见上方"数据状态"重跑指南） |
| 链接层 precision ≥98% | ⏳ | 待 `docs/golden-set-sample.csv` 100 条人工标注 |
| 市场分层字段可用 | ✅ | `domain.market_group` 字段已就位，14 国分布合理 |
| ≥100 条中国出海 P0 | ⏳ | 依赖金标准集标注；代码已实现 P0 第 1 强信号（developer_name）作为补充判定 |