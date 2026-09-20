#!/usr/bin/env bash
# =============================================================================
# 从备份恢复 PostgreSQL（scripts/backup.sh 产出的 .sql.gz）
# 用法：scripts/restore.sh backups/db-20260101-120000.sql.gz
#
# 行为：停 api/worker（避免恢复期间写入）→ DROP 并重建目标库 → 灌入 dump →
#       重启（api 启动时自动 alembic upgrade head；dump 已含 alembic_version，
#       版本一致时为空操作，旧 dump 会被自动补迁到最新 schema）。
# 警告：目标库现有数据将被清空。
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"

require_docker
require_env

DUMP="${1:-}"
if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then
  err "用法：$0 <backup.sh 产出的 db-*.sql.gz 文件路径>"
  [ -n "$DUMP" ] && err "文件不存在：${DUMP}"
  exit 1
fi
DUMP="$(cd "$(dirname "$DUMP")" && pwd)/$(basename "$DUMP")"

PG_USER="$(env_value POSTGRES_USER gold)"
PG_DB="$(env_value POSTGRES_DB gold_desire)"

warn "即将清空并恢复数据库 ${PG_DB}（来自 ${DUMP}），现有数据将丢失。"
printf '%s' "确认继续？输入 yes 回车："
read -r answer
[ "$answer" = "yes" ] || { err "已取消"; exit 1; }

step "1/4 停止 api / worker（保留 postgres）"
dc stop api worker

step "2/4 重建数据库 ${PG_DB}"
# 连接 maintenance 库 postgres 执行 DROP/CREATE；FORCE 断开残留连接（PG13+）
dc exec -T postgres psql -U "$PG_USER" -d postgres \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${PG_DB}' AND pid<>pg_backend_pid();" \
  -c "DROP DATABASE IF EXISTS \"${PG_DB}\";" \
  -c "CREATE DATABASE \"${PG_DB}\";"

step "3/4 灌入备份（gunzip → psql）"
gunzip -c "$DUMP" | dc exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -q

step "4/4 重启服务（api 启动时自动补齐迁移）"
dc up -d
if ! wait_ready 180; then
  err "恢复后未就绪，请查看日志：docker compose -p gold-desire logs --tail 100 api postgres"
  exit 1
fi
ok "恢复完成：${DUMP} → ${PG_DB}"
