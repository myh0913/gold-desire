#!/usr/bin/env bash
# =============================================================================
# 重启服务（容器原地重启；api 重启时会再次执行 alembic upgrade head，幂等安全）
# 用法：scripts/restart.sh [服务名...]（缺省全部）
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"

require_docker
require_env

# 仅改了 .env 时推荐：配置经环境注入，restart 即生效，无需重建镜像
if [ "${1:-}" = "env" ]; then
  step "应用 .env 变更（recreate 容器以注入新环境）"
  dc up -d --force-recreate api worker nginx migrate 2>/dev/null || dc up -d --force-recreate api worker nginx
  wait_ready 120
  ok "配置已生效"
  exit 0
fi

step "重启 gold-desire：${*:-全部服务}"
if [ $# -gt 0 ]; then
  dc restart "$@"
else
  dc restart
fi
wait_ready 120
dc ps
ok "重启完成"
