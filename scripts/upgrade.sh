#!/usr/bin/env bash
# =============================================================================
# 升级部署：拉代码 → 重建镜像 → 显式迁移 → 滚动重启 → 就绪等待
# 用法：scripts/upgrade.sh [--no-pull]（--no-pull 跳过 git pull，用于本地改码后升级）
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"

require_docker
require_env

step "1/5 拉取最新代码"
if [ "${1:-}" = "--no-pull" ]; then
  info "跳过 git pull（--no-pull）"
elif git -C "$GD_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  git -C "$GD_ROOT" pull --ff-only
else
  info "非 git 仓库，跳过拉取（手工同步代码后重跑）"
fi

step "2/5 重建镜像"
dc build

step "3/5 显式数据库迁移（docker compose run --rm api alembic upgrade head）"
dc run --rm api alembic upgrade head

step "4/5 滚动重启（up -d 只重建镜像/配置变化了的容器，未变化的保持运行）"
dc up -d

step "5/5 等待就绪"
if ! wait_ready 180; then
  err "升级后未就绪。回滚参考 docs/deploy-ops.md「故障排查」："
  err "  1) dc run --rm --no-deps api alembic downgrade -1   # 迁移回退一步"
  err "  2) git -C ${GD_ROOT} checkout <上一个版本> && scripts/upgrade.sh --no-pull"
  exit 1
fi

dc ps
ok "升级完成（策略/因子参数类改动无需升级，配置中心热生效）"
