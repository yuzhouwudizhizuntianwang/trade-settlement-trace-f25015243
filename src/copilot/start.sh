#!/usr/bin/env bash
# 生产/托管启动脚本：从平台注入的 $PORT 取端口，绑定 0.0.0.0
# 本地无 .env 时用演示默认值，保证"拉下来就能跑"；真实部署请在平台配置环境变量覆盖。
set -euo pipefail

export COPILOT_JWT_SECRET="${COPILOT_JWT_SECRET:-deploy-demo-secret-change-me-0123456789}"
export COPILOT_DEMO_PWD="${COPILOT_DEMO_PWD:-Demo#2026}"
export COPILOT_LLM_BASE_URL="${COPILOT_LLM_BASE_URL:-http://127.0.0.1:11434/v1}"
export COPILOT_LLM_API_KEY="${COPILOT_LLM_API_KEY:-ollama}"

PORT="${PORT:-8000}"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
