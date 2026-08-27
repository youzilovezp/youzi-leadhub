#!/usr/bin/env bash
# 本地开发启动脚本
# 端口从 .env 中的 PORT 字段读取，避免与 Makefile/backend-dev 行为不一致
set -e

cd "$(dirname "$0")/.."

if [ ! -f ".env" ]; then
    echo "❌ .env 文件不存在，请先：cp .env.example .env"
    exit 1
fi

# 从 .env 读取 PORT，默认 8000
PORT="$(grep -E '^PORT=' .env | cut -d= -f2 | tr -d '[:space:]')"
PORT="${PORT:-8000}"

echo "🚀 启动开发服务器（端口：$PORT）..."
exec uvicorn app.main:app --reload --host 0.0.0.0 --port "$PORT"
