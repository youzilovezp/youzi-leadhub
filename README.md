# youzi-bsp — WhatsApp BSP 线索获取管道

依据 [`research-2026-09/whatsapp-bsp-leadgen-可行性分析.md`](../research-2026-09/whatsapp-bsp-leadgen-可行性分析.md)（v6.1）实施。
获取"在用 WhatsApp 承接客户的海外企业"线索：**不限中国出海（P0 优先层），全量入库分层打分**。

> ⚠️ **合规红线（报告第七节）**：本工具只做**线索发现与准备**，不包含任何外联/发送能力。
> 绝不对爬到的号码做 WhatsApp 群发/自动化营销；不引入反爬绕过层，被挑战即弃站。

## 技术选型（GitHub 视角全新评估，不重复造轮子）

| 层 | 选型 | 理由 |
|----|------|------|
| 爬取调度 | **Scrapy** | robots 遵守/自动限速/重试/去重/断点（JOBDIR）/大响应截断全内置 |
| HTML 解析 | **parsel**（Scrapy 自带，lxml 底座） | 链接提取用成熟解析器，非正则 |
| 号码归一 | **phonenumbers** | Google libphonenumber 官方移植，E164/有效性判定 |
| 域名归一 | **tldextract** | PSL 标准实现（含私有段 → myshopify 回退 host 粒度） |
| 种子下载 | **httpx** | Tranco zip / Common Crawl CDX REST |
| App 种子 | **google-play-scraper**（可选） | 按国家×类目分榜（总榜被大 app 霸榜） |
| API | **FastAPI + uvicorn** | 只读 /api/stats、/api/leads |
| 前端 | **@appica/ui-react**（默认配色）+ React 19 + Tailwind v4 + Vite | 用户指定 UI 库 |
| 测试 | **pytest** | 业界标准 |

自写仅业务层（GitHub 无现成轮子）：两层检测（~100 行）、分层打分（~80 行）、三表 schema、种子渠道编排。

## 快速开始

```bash
cd youzi-bsp
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/        # 36 项全绿（两层检测/归一/幂等/打分/预算/SSRF 边界）
```

## D1–3：三渠道命中率对比（报告第八节）

```bash
# 1) 生成种子（各渠道 1–2 千域名）
.venv/bin/python -m youzi_bsp seed tranco --top 2000
.venv/bin/python -m youzi_bsp seed myshopify --limit 2000
.venv/bin/python -m youzi_bsp seed play --country id --category BUSINESS --limit 200   # 需可选依赖
.venv/bin/python -m youzi_bsp seed sample --file your-list.txt                        # 手工清单

# 2) 分渠道爬取（礼貌：robots/自动限速/每站≤5页/超2MB丢弃/8s超时）
.venv/bin/python -m youzi_bsp crawl --seed-file data/seeds-tranco.txt --channel tranco --limit 1000
.venv/bin/python -m youzi_bsp crawl --seed-file data/seeds-myshopify.txt --channel myshopify --limit 1000
# 断点续跑：加 --jobdir data/job1（Scrapy JOBDIR：队列/去重指纹持久化，重跑同目录续传）

# 3) 命中率统计 + 导出
.venv/bin/python -m youzi_bsp stats
.venv/bin/python -m youzi_bsp export --out data/leads.csv
```

参考基线（待实测校准，报告 3.2）：Shopify ≥8%、垂直目录 ≥5%、Tranco ≥2%。

## Web 面板（appica-ui，默认配色）

```bash
# API + 前端产物（frontend/dist 已构建，FastAPI 直接托管）
BSP_DB=data/leads.db .venv/bin/uvicorn youzi_bsp.api:app --port 8788
# 打开 http://127.0.0.1:8788

# 前端开发态（热更新，/api 代理到 8788）
cd frontend && pnpm install && pnpm dev   # http://localhost:5173
```

## 数据模型（SQLite，PG 兼容三表）

```
domain(entity_key, channel, seed_host, status, widget, p0, market, lang, score, first_seen, last_crawled)
phone(e164, valid, country)
sighting(entity_key, e164, url, layer, first_seen, last_seen)   -- 多对多证据表 = 合规留痕
```

幂等：全部 upsert——同批重跑不产生重复 sighting。

## 检测能力边界（报告 v6.1）

- 两层扫描：链接层（`wa.me/`、`api.whatsapp.com/send/?phone=`、`whatsapp://send`）+ 文本层（"whatsapp" 关键词 ±100 字符、仅国际格式）
- **SaaS widget（Elfsight/Tidio）静态不可见**（号码运行时从厂商 API 拉取）——只记指纹；真实漏检率用 200 站 Playwright 校准（>15% 才升级引擎）
- app-only 无官网的商家不可达（范围边界，非漏检）
- **建议跑在海外 VPS**：CN 出口会被部分站点降级/拒绝（冒烟中 2 个 SEA 站超时即此因）
