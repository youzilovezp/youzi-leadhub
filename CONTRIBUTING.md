# 贡献指南

## 开发环境

```bash
git clone https://github.com/youzilovezp/youzi-leadhub.git && cd youzi-leadhub
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 前端（仅改 frontend/ 时需要）
cd frontend && pnpm i
```

- Python 3.13+，Node 20+ / pnpm 9+
- Play 渠道需要可选依赖：`pip install google-play-scraper`

## 提交前

```bash
.venv/bin/python -m pytest tests/ -q     # 必须全绿
cd frontend && pnpm build                # 改了前端时必须通过
```

- 新逻辑带最小测试（参考 `tests/` 现有风格，不追求覆盖率数字）
- 提交信息遵循 Conventional Commits：`feat:` / `fix:` / `docs:` / `chore:` …
- SQL 一律走 upsert（`ON CONFLICT`），保持幂等写入

## 两条红线

评审直接拒收，不解释：

1. **不加反爬绕过**——被站点挑战就弃站，不引入指纹伪装 / 验证码求解
2. **不加外发能力**——本工具只做线索发现与准备，不代发任何消息

## 目录速览

```
app/            后端：api.py(API) · cli.py(命令行) · smart_crawler.py(智能调度)
                crawl_api.py(爬取编排) · pipelines.py(入库) · seeds.py(种子) · db.py
frontend/src/   React 面板
tests/          pytest（检测 / 归一 / 管道 / 调度 / 中间件 / 金标准）
docs/           内部资料（gitignore，不随仓库分发）
```
