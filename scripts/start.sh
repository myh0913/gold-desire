#!/usr/bin/env bash
# =============================================================================
# 启动服务（已部署过；首次部署请用 deploy.sh）
# 用法：scripts/start.sh
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"

require_docker
require_env

step "启动 gold-desire（postgres/redis → api → worker → nginx）"
dc up -d
wait_ready 120
dc ps
ok "已启动。日志：scripts/目录下执行 docker compose -p gold-desire logs -f api worker"
