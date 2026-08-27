# Leadhub 常用命令
# 设计原则：默认 PostgreSQL；make start 优先复用本机已运行的中间件，缺的才用 Docker 起

# =============== admin mode ===============
ENV_FILE := backend/.env
# venv 里的 python（make install / backend-dev 创建）；没有则退回 python3
VENV_PY := $(wildcard backend/.venv/bin/python)
BPY := $(if $(VENV_PY),backend/.venv/bin/python,python3)
.PHONY: help backend-dev frontend-dev install db-migrate db-upgrade db-downgrade reset-admin admin-pass test dev use-sqlite use-pg start stop backup restore

# 从 .env 读中间件端口（没写用默认值）
PG_PORT := $(shell sed -n 's/^POSTGRES_PORT=//p' $(ENV_FILE) 2>/dev/null | head -1 | awk '{print $$1}')
REDIS_PORT := $(shell sed -n 's/^REDIS_PORT=//p' $(ENV_FILE) 2>/dev/null | head -1 | awk '{print $$1}')
PG_PORT := $(if $(PG_PORT),$(PG_PORT),5432)
REDIS_PORT := $(if $(REDIS_PORT),$(REDIS_PORT),6379)
# 服务端口从 .env 读（改 .env 的 PORT / FRONTEND_PORT 即生效，支持多项目并存）
BACKEND_PORT := $(shell sed -n 's/^PORT=//p' $(ENV_FILE) 2>/dev/null | head -1 | awk '{print $$1}')
BACKEND_PORT := $(if $(BACKEND_PORT),$(BACKEND_PORT),8000)
FRONTEND_PORT := $(shell sed -n 's/^FRONTEND_PORT=//p' $(ENV_FILE) 2>/dev/null | head -1 | awk '{print $$1}')
FRONTEND_PORT := $(if $(FRONTEND_PORT),$(FRONTEND_PORT),3000)

# 端口探测（python3 socket，跨平台无依赖）：exit 0 = 有人监听（可复用）
port_listening = python3 -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(('127.0.0.1', $(1))) == 0 else 1)"

help:           ## 显示帮助
	@echo "可用命令（默认 PostgreSQL；make start 复用本机已有中间件，缺的用 Docker 起）："
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install:        ## 安装依赖（后端 venv + 前端 node_modules）
	@python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" || \
		{ echo "❌ Python $$(python3 --version 2>&1) 过低：本项目需要 3.11+（python.org 下载）"; exit 1; }
	cd backend && (test -d .venv || uv venv .venv 2>/dev/null || python3 -m venv .venv) && \
	. .venv/bin/activate && \
	(uv pip install -e ".[dev]" 2>/dev/null || pip install -e ".[dev]")
	@if command -v pnpm >/dev/null 2>&1; then \
		cd frontend && pnpm install; \
	elif command -v npm >/dev/null 2>&1; then \
		echo "ℹ️  未装 pnpm，用 npm 安装（可 npm install -g pnpm 提速）"; cd frontend && npm install; \
	else \
		echo "❌ 前端依赖安装失败：pnpm / npm 都不可用——先装 Node.js LTS（nodejs.org）"; exit 1; \
	fi

# 依赖 start：先确保中间件就绪（复用本机 / Docker 起），再启动后端
backend-dev: start  ## 启动后端（自动准备中间件 + 建表 + 种子）
	cd backend && (test -d .venv || uv venv .venv 2>/dev/null || python3 -m venv .venv) && \
	(test -f .env || cp .env.example .env) && \
	. .venv/bin/activate && \
	(uv pip install -e ".[dev]" 2>/dev/null || pip install -e ".[dev]") && \
	uvicorn app.main:app --reload --host 0.0.0.0 --port $(BACKEND_PORT)

frontend-dev:   ## 启动前端
	cd frontend && (pnpm dev --port $(FRONTEND_PORT) 2>/dev/null || npm run dev -- --port $(FRONTEND_PORT))

# 生成迁移文件：make db-migrate MSG="add order"
db-migrate:     ## 生成新迁移（MSG="描述"）
	@if [ -z "$(MSG)" ]; then \
		echo "❌ 必须传 MSG: make db-migrate MSG=\"add order\""; \
		exit 1; \
	fi
	cd backend && $(if $(VENV_PY),.venv/bin/python,python3) -m alembic revision --autogenerate -m "$(MSG)"; \
	f=$$(ls -t backend/alembic/versions/*.py 2>/dev/null | head -1); \
	if [ -n "$$f" ] && ! grep -q "op\.\|sa\." "$$f"; then \
		echo "⚠️  本次迁移是空的（没有检测到表结构变更）。最常见原因：新 Model 没在 backend/app/models/__init__.py 注册"; \
		echo "   请检查：grep 'from app.models.<模块名>' backend/app/models/__init__.py"; \
	fi

# 应用所有迁移
db-upgrade:     ## 应用所有迁移到最新
	cd backend && $(if $(VENV_PY),.venv/bin/python,python3) -m alembic upgrade head

# 回滚一步（⚠️ 危险：会 drop 上一 migration 创建的对象）
db-downgrade:   ## 回滚最近一步迁移
	@echo "⚠️ db-downgrade 会撤销上一 migration 的所有变更（drop table / column）"
	@echo "   仅用于本地调试；生产请用备份恢复"
	@read -p "确认回滚? [y/N] " ans && [ "$$ans" = "y" ] || (echo "❌ 已取消"; exit 1)
	cd backend && $(if $(VENV_PY),.venv/bin/python,python3) -m alembic downgrade -1

reset-admin:    ## 重置 admin 密码（不传则重置为 admin）
	cd backend && $(if $(VENV_PY),.venv/bin/python,python3) scripts/reset_admin.py

# 指定密码：make admin-pass NEW=YourPass!2025
admin-pass:     ## 重置 admin 密码为 NEW=... 指定值
	cd backend && $(if $(VENV_PY),.venv/bin/python,python3) scripts/reset_admin.py --password "$(NEW)"

test: install  ## 跑后端 + 前端测试（用独立临时测试库，不碰开发数据、不需要中间件）
	cd backend && $(if $(VENV_PY),.venv/bin/python,python3) -m pytest -q
	cd frontend && (pnpm test 2>/dev/null || npm test)

# 一条命令同时启动前后端（Ctrl+C 一起停）
dev: install start  ## 一键启动：后端 + 前端（Ctrl+C 全部停止）
	@trap 'kill 0' INT TERM; \
	( cd backend && . .venv/bin/activate 2>/dev/null; exec uvicorn app.main:app --reload --host 0.0.0.0 --port $(BACKEND_PORT) ) & \
	( cd frontend && \
	  if command -v pnpm >/dev/null 2>&1; then exec pnpm dev --port $(FRONTEND_PORT); \
	  elif command -v npm >/dev/null 2>&1; then exec npm run dev -- --port $(FRONTEND_PORT); \
	  else echo "❌ 前端未启动：pnpm / npm 都不可用（装 Node.js：nodejs.org）"; exit 1; fi ) & \
	wait

use-sqlite:     ## 切换到 SQLite（零依赖单文件，免中间件）
	@python3 -c "import re,pathlib; p=pathlib.Path('$(ENV_FILE)'); t=p.read_text(); p.write_text(re.sub(r'^DB_TYPE=.*','DB_TYPE=sqlite',t,flags=re.M)); print('✅ 已切换 SQLite（数据存 backend/data/app.db），make start 不再需要中间件')"

use-pg:         ## 切换回 PostgreSQL（默认）
	@python3 -c "import re,pathlib; p=pathlib.Path('$(ENV_FILE)'); t=p.read_text(); p.write_text(re.sub(r'^DB_TYPE=.*','DB_TYPE=postgresql',t,flags=re.M)); print('✅ 已切换 PostgreSQL，make start 复用本机 / 起 Docker')"

backup:         ## 备份数据库到 backups/（PG 用 pg_dump；SQLite 复制文件）
	@mkdir -p backups && ts=$$(date +%Y%m%d_%H%M%S); \
	dbtype=$$(sed -n 's/^DB_TYPE=//p' $(ENV_FILE) 2>/dev/null | head -1); \
	if [ "$${dbtype:-postgresql}" = "sqlite" ]; then \
		if [ -f backend/data/app.db ]; then \
			python3 -c "import sqlite3; sqlite3.connect('backend/data/app.db').backup(sqlite3.connect('backups/app_$$ts.db'))" && echo "✅ 已备份到 backups/app_$$ts.db"; \
		else \
			echo "ℹ️  还没有数据库文件（首次启动后自动生成）"; \
		fi; \
	else \
		user=$$(sed -n 's/^POSTGRES_USER=//p' $(ENV_FILE) | head -1); \
		db=$$(sed -n 's/^POSTGRES_DB=//p' $(ENV_FILE) | head -1); \
		port=$$(sed -n 's/^POSTGRES_PORT=//p' $(ENV_FILE) | head -1); \
		if docker compose --env-file $(ENV_FILE) ps --status running postgres 2>/dev/null | grep -q postgres; then \
			docker compose --env-file $(ENV_FILE) exec -T postgres pg_dump -U "$${user:-youzi-leadhub}" "$${db:-youzi-leadhub}" > "backups/app_$$ts.sql"; \
			if grep -q "^COPY" "backups/app_$$ts.sql"; then echo "✅ 已备份到 backups/app_$$ts.sql"; \
			else echo "⚠️  备份完成但库是空的（没有业务数据）——先启动过后端再备份"; fi; \
		else \
			if ! command -v pg_dump >/dev/null 2>&1; then echo "❌ 备份需要 pg_dump（PostgreSQL 客户端工具）；装 PostgreSQL 或改用 Docker 模式"; rm -f "backups/app_$$ts.sql"; exit 1; fi; \
			PGPASSWORD=$$(sed -n 's/^POSTGRES_PASSWORD=//p' $(ENV_FILE) | head -1) pg_dump -h 127.0.0.1 -p "$${port:-$(PG_PORT)}" -U "$${user:-youzi-leadhub}" "$${db:-youzi-leadhub}" > "backups/app_$$ts.sql" && echo "✅ 已备份到 backups/app_$$ts.sql"; \
		fi; \
	fi

# 恢复备份：make restore FILE=backups/app_20260825_220000.sql（或 .db）
restore:        ## 恢复备份（FILE=backups/xxx.sql 或 .db）
	@if [ -z "$(FILE)" ]; then echo "❌ 用法: make restore FILE=backups/app_xxx.sql"; exit 1; fi; \
	if [ ! -f "$(FILE)" ]; then echo "❌ 文件不存在: $(FILE)"; exit 1; fi; \
	case "$(FILE)" in \
	  *.db) python3 -c "import sqlite3,shutil; shutil.copy('$(FILE)','backend/data/app.db'); sqlite3.connect('backend/data/app.db').execute('PRAGMA integrity_check')" && echo "✅ SQLite 已恢复到 backend/data/app.db（必须重启后端进程生效——旧进程持有旧文件句柄）";; \
	  *.sql) user=$$(sed -n 's/^POSTGRES_USER=//p' $(ENV_FILE) | head -1 | awk '{print $$1}'); \
	    db=$$(sed -n 's/^POSTGRES_DB=//p' $(ENV_FILE) | head -1 | awk '{print $$1}'); \
	    DROP_CMD="DROP SCHEMA public CASCADE; CREATE SCHEMA public;"; \
	    if docker compose --env-file $(ENV_FILE) ps --status running postgres 2>/dev/null | grep -q postgres; then \
	      echo "⚠️  先清空目标库（DROP SCHEMA public）再导入——当前库数据将被备份文件覆盖"; \
	      docker compose --env-file $(ENV_FILE) exec -T postgres psql -U "$${user:-youzi-leadhub}" -d "$${db:-youzi-leadhub}" -c "$$DROP_CMD" >/dev/null 2>&1; \
	      docker compose --env-file $(ENV_FILE) exec -T postgres psql -U "$${user:-youzi-leadhub}" -d "$${db:-youzi-leadhub}" -v ON_ERROR_STOP=1 < "$(FILE)" && echo "✅ PG 已恢复（Docker 实例）"; \
	    else \
	      if ! command -v psql >/dev/null 2>&1; then echo "❌ 复用本机 PG 恢复需要 psql 客户端；先 make start 再恢复（Docker 模式）"; exit 1; fi; \
	      echo "⚠️  先清空目标库（DROP SCHEMA public）再导入——当前库数据将被备份文件覆盖"; \
	      PGPASSWORD=$$(sed -n 's/^POSTGRES_PASSWORD=//p' $(ENV_FILE) | head -1 | awk '{print $$1}') psql -h 127.0.0.1 -p $(PG_PORT) -U "$${user:-youzi-leadhub}" -d "$${db:-youzi-leadhub}" -c "$$DROP_CMD" >/dev/null 2>&1; \
	      PGPASSWORD=$$(sed -n 's/^POSTGRES_PASSWORD=//p' $(ENV_FILE) | head -1 | awk '{print $$1}') psql -h 127.0.0.1 -p $(PG_PORT) -U "$${user:-youzi-leadhub}" -d "$${db:-youzi-leadhub}" -v ON_ERROR_STOP=1 < "$(FILE)" && echo "✅ PG 已恢复（本机实例）"; \
	    fi;; \
	  *) echo "❌ 只支持 .sql / .db 备份文件"; exit 1;; \
	esac

# 中间件启停：读 .env 实时配置。本机已有 PostgreSQL/Redis 直接复用；缺的才用 Docker 起。
start:          ## 启动中间件（优先复用本机已运行的 PG/Redis）
	@dbtype=$$(sed -n 's/^DB_TYPE=//p' $(ENV_FILE) 2>/dev/null | head -1); \
	redis_host=$$(sed -n 's/^REDIS_HOST=//p' $(ENV_FILE) 2>/dev/null | head -1); \
	needs=""; \
	if [ "$${dbtype:-postgresql}" = "postgresql" ]; then needs="$$needs pg"; fi; \
	if [ -n "$$redis_host" ]; then needs="$$needs redis"; fi; \
	if [ -z "$$needs" ]; then \
		echo "ℹ️  当前配置走 SQLite + 内存模式，无需中间件。"; \
		exit 0; \
	fi; \
	services=""; \
	for n in $$needs; do \
		case $$n in \
			pg) \
				if $(call port_listening,$(PG_PORT)); then \
					echo "♻️  复用本机已运行的 PostgreSQL（127.0.0.1:$(PG_PORT)）"; \
					echo "   ⚠️ 前提：该实例已存在 .env 里的 POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB，否则连接会失败"; \
					echo "      不想用本机的？改 .env 的 POSTGRES_PORT 为空闲端口（如 15432），重新 make start 即用 Docker 起独立实例"; \
				else \
					services="$$services postgres"; \
				fi ;; \
			redis) \
				if $(call port_listening,$(REDIS_PORT)); then \
					echo "♻️  复用本机已运行的 Redis（127.0.0.1:$(REDIS_PORT)）；密码需与 .env REDIS_PASSWORD 一致"; \
				else \
					services="$$services redis"; \
				fi ;; \
		esac; \
	done; \
	if [ -n "$$services" ]; then \
		if ! command -v docker >/dev/null 2>&1; then \
			echo "❌ 中间件[$$services]本机未运行，且未安装 Docker"; \
			echo "   解决：装 Docker（docker.com），或把 .env 指向本机已运行的中间件"; \
			exit 1; \
		fi; \
		if ! docker info >/dev/null 2>&1; then \
			echo "❌ Docker 已装但没启动——打开 Docker Desktop，等图标就绪后重试"; \
			exit 1; \
		fi; \
		echo "🐳 Docker 启动缺少的中间件:$$services"; \
		docker compose --env-file $(ENV_FILE) up -d $$services; \
	else \
		echo "✅ 中间件全部就绪（均为本机已有服务，未启动任何容器）"; \
	fi; \
	echo "   数据库 UI adminer（可选）：docker compose --env-file $(ENV_FILE) up -d adminer → http://localhost:8080"

stop:           ## 停止 Docker 中间件容器（本机服务不受影响）
	@docker compose down 2>/dev/null || echo "ℹ️  没有需要停止的容器"

