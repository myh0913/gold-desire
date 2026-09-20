#!/usr/bin/env bash
# =============================================================================
# 停止服务（保留容器与数据卷；restart=unless-stopped 的容器重启机器后仍会自启）
# 用法：scripts/stop.sh
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"

require_docker
require_env

step "停止 gold-desire 全部服务（数据卷 pg_data / redis_data 保留不动）"
dc stop
ok "已停止。彻底移除容器（数据仍在卷里）：docker compose -p gold-desire down"
