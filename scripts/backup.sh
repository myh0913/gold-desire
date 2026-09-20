#!/usr/bin/env bash
# =============================================================================
# PostgreSQL 逻辑备份：pg_dump（postgres 容器内执行）→ gzip → backups/ 滚动保留
# 用法：scripts/backup.sh            （保留最近 BACKUP_KEEP 份，.env 可调，默认 14）
#       BACKUP_KEEP=30 scripts/backup.sh
# 恢复：scripts/restore.sh backups/db-<时间戳>.sql.gz
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"

require_docker
require_env

PG_USER="$(env_value POSTGRES_USER gold)"
PG_DB="$(env_value POSTGRES_DB gold_desire)"
KEEP="$(env_value BACKUP_KEEP 14)"

mkdir -p "$GD_BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${GD_BACKUP_DIR}/db-${STAMP}.sql.gz"

step "pg_dump → ${OUT}"
# -T: 禁用 TTY（管道输出必须）；--no-owner/--no-privileges: 恢复时不依赖角色存在
dc exec -T postgres pg_dump -U "$PG_USER" --no-owner --no-privileges "$PG_DB" | gzip > "$OUT"

if [ ! -s "$OUT" ]; then
  err "备份文件为空，疑似 pg_dump 失败（检查：dc logs --tail 50 postgres）"
  exit 1
fi

step "按保留策略清理（保留最近 ${KEEP} 份）"
# ls 按名字排序 = 按时间戳排序（db-YYYYmmdd-HHMMSS 前缀）
ls -1 "${GD_BACKUP_DIR}"/db-*.sql.gz 2>/dev/null | head -n -"$KEEP" | while read -r old; do
  info "删除过期备份：${old}"
  rm -f "$old"
done

ok "备份完成：${OUT}（$(du -h "$OUT" | cut -f1)）"
info "提示：backups/ 含敏感数据，建议加入 .gitignore 并异地同步（rsync/对象存储）"
