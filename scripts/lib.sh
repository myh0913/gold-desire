#!/usr/bin/env bash
# =============================================================================
# gold-desire 运维脚本共享库（被 scripts/*.sh source，不单独执行）
# 统一：仓库根定位 / 彩色日志 / docker compose 调用 / 就绪等待 / .env 读取
# =============================================================================
set -euo pipefail

# 仓库根：脚本位于 gold-desire/scripts/，从任意 cwd 调用均可
GD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GD_ENV_FILE="${GD_ROOT}/.env"
GD_ENV_EXAMPLE="${GD_ROOT}/.env.example"
GD_BACKUP_DIR="${GD_ROOT}/backups"
GD_PROJECT_NAME="gold-desire"

# ---------------------------------------------------------------- 颜色日志
if [ -t 1 ]; then
  C_RESET=$'\033[0m'
  C_RED=$'\033[31m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_BLUE=$'\033[34m'
  C_CYAN=$'\033[36m'
  C_BOLD=$'\033[1m'
else
  C_RESET="" C_RED="" C_GREEN="" C_YELLOW="" C_BLUE="" C_CYAN="" C_BOLD=""
fi

info() { printf '%s\n' "${C_BLUE}[INFO]${C_RESET} $*"; }
ok()   { printf '%s\n' "${C_GREEN}[ OK ]${C_RESET} $*"; }
warn() { printf '%s\n' "${C_YELLOW}[WARN]${C_RESET} $*" >&2; }
err()  { printf '%s\n' "${C_RED}[FAIL]${C_RESET} $*" >&2; }
step() { printf '\n%s\n' "${C_BOLD}==> $*${C_RESET}"; }

# ---------------------------------------------------------------- 前置检查
require_docker() {
  command -v docker >/dev/null 2>&1 || {
    err "未安装 Docker（参考 https://docs.docker.com/engine/install/ 安装）"
    exit 1
  }
  docker compose version >/dev/null 2>&1 || {
    err "未安装 Docker Compose v2（apt install docker-compose-plugin 或等同方式）"
    exit 1
  }
}

require_env() {
  [ -f "$GD_ENV_FILE" ] || {
    err "缺少 ${GD_ENV_FILE}：先执行 cp ${GD_ENV_EXAMPLE} ${GD_ENV_FILE} 并填写密钥，"
    err "或直接运行 scripts/deploy.sh（首次会自动复制模板）"
    exit 1
  }
}

# ---------------------------------------------------------------- compose 封装
# 统一 project-name / env-file；相对路径（../.env、nginx.conf、frontend/dist）
# 以 deploy/（首个 compose 文件所在目录）为基准解析，手工在 deploy/ 下裸跑亦同口径。
# 追加 override（如宿主机构建前端）：
#   GD_COMPOSE_FILES="deploy/docker-compose.yml deploy/compose.host-build.yml" ./scripts/start.sh
dc() {
  # shellcheck disable=SC2206
  local files=(${GD_COMPOSE_FILES:-deploy/docker-compose.yml})
  local args=(
    compose --project-name "$GD_PROJECT_NAME"
    --env-file "$GD_ENV_FILE"
  )
  local f
  for f in "${files[@]}"; do
    args+=(-f "${GD_ROOT}/${f}")
  done
  docker "${args[@]}" "$@"
}

# ---------------------------------------------------------------- .env 读取
# env_value KEY [DEFAULT] —— 从 .env 取值（不存在/为空时回退默认）
env_value() {
  local key="$1" default="${2:-}" value=""
  if [ -f "$GD_ENV_FILE" ]; then
    value="$(grep -E "^${key}=" "$GD_ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- || true)"
  fi
  if [ -n "$value" ]; then
    printf '%s' "$value"
  else
    printf '%s' "$default"
  fi
}

# ---------------------------------------------------------------- 就绪等待
# wait_ready [TIMEOUT_SECONDS] —— 探测 nginx → api 的 /ready 整链路
wait_ready() {
  local timeout="${1:-120}"
  local port elapsed=0
  port="$(env_value NGINX_HTTP_PORT 80)"
  command -v curl >/dev/null 2>&1 || {
    warn "宿主机无 curl，跳过就绪探测（请手动访问 /ready 确认）"
    return 0
  }
  info "等待 http://127.0.0.1:${port}/ready 就绪（最长 ${timeout}s）..."
  while [ "$elapsed" -lt "$timeout" ]; do
    if curl -fsS "http://127.0.0.1:${port}/ready" >/dev/null 2>&1; then
      ok "服务已就绪（/ready = 200）"
      return 0
    fi
    sleep 3
    elapsed=$((elapsed + 3))
  done
  err "就绪探针超时（${timeout}s）。排查命令："
  err "  ${GD_ROOT}/scripts/restart.sh 前先看日志：docker compose -p ${GD_PROJECT_NAME} logs --tail 100 api postgres redis nginx"
  return 1
}
