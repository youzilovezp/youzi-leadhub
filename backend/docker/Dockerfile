# ---------- Stage 1: builder ----------
# 锁小版本号（pin minor + patch），生产审计时知道用了哪个具体 build
# 3.11.13-slim = 2025-04 最新 patch，含 cpython 多个安全修复
FROM python:3.11.13-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 一起 copy pyproject + readme + app 代码：hatchling 构建 wheel 时需要读取 readme 和 packages=["app"]
COPY pyproject.toml 后端说明.md ./
COPY requirements.lock.txt ./
COPY app ./app
COPY alembic ./alembic

# 冻结安装：依赖全部来自 uv export 的带哈希锁文件（可复现构建 + 供应链完整性
# 校验），应用本体 --no-deps 装入（依赖已由锁文件钉死，不允许构建时重新解析）
RUN pip install --upgrade "pip>=24.0" && \
    pip install --prefix=/install --require-hashes -r requirements.lock.txt && \
    pip install --prefix=/install --no-deps .

# ---------- Stage 2: runtime ----------
# 用 debian-slim 而非 alpine：避免 asyncpg / bcrypt / cryptography musl 兼容坑
FROM python:3.11.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 装 curl 给 HEALTHCHECK 用（slim 镜像不自带 wget/curl）
# runtime 仅 +1MB，比每次探针 fork Python 解释器省资源
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 非 root 用户运行（生产硬要求）+ 固定 uid=1000 方便 volume 挂载权限对齐
RUN groupadd --system --gid 1000 app && \
    useradd --system --uid 1000 --gid 1000 --create-home app

WORKDIR /app

# 从 builder 阶段拷 site-packages（已含 app + alembic + 所有 deps）
COPY --from=builder /install /usr/local
# 精确 COPY 需要的目录（避免把整个 backend 目录扔进镜像）
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app alembic.ini ./alembic.ini
COPY --chown=app:app app ./app
COPY --chown=app:app 后端说明.md ./

# 确保 WORKDIR 可被 app 用户写（uvicorn worker tmp、gunicorn heartbeat）
RUN chown -R app:app /app

USER app

EXPOSE 8000

# HEALTHCHECK 用 /healthz（liveness 语义：进程是否能响应）
# /readyz 含 DB ping，应留给 K8s readinessProbe 使用（避免 DB 短暂不可用
# 时整个 pod 被重启——restart 应该用 /healthz 永不 fail 的端点）
# 改用 curl 而非 python urllib（少 fork 进程，1MB 镜像换更好性能）
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# gunicorn 配置：
# - WORKERS 从环境变量读（默认 1：任务系统是单进程设计——WORKERS>1 时
#   TaskRunner/调度器被禁用，采集任务会停在 queued；见 app/main.py lifespan）
# - --max-requests 防 Python 进程长时间累积内存
# - --keep-alive 与 LB / Nginx 默认 idle timeout 对齐
# - --access-logformat 含响应时间 microseconds 便于 ELK/Loki 关联
# - timeout 120s（兼容 alembic 迁移、批量导入、备份恢复等长任务）
ENTRYPOINT ["sh", "-c", "exec gunicorn app.main:app \
    -w \"${WORKERS:-1}\" \
    -k uvicorn.workers.UvicornWorker \
    -b 0.0.0.0:8000 \
    --timeout 120 \
    --graceful-timeout 60 \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    --keep-alive 5 \
    --access-logfile - \
    --error-logfile - \
    --access-logformat '%(h)s %(l)s %(u)s %(t)s \"%(r)s\" %(s)s %(b)s \"%(f)s\" \"%(a)s\" %(L)s %(D)sus'"]
# 注意：ENTRYPOINT 用 sh -c 让环境变量 ${WORKERS:-4} 展开
# exec 替换 shell 进程为 gunicorn（让信号正确传递）
