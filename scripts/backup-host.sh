#!/usr/bin/env bash
# =============================================================================
# 本机 PostgreSQL 逻辑备份（非 Docker 部署用）：pg_dump → gzip → 滚动保留
# 用法：scripts/backup-host.sh          （保留最近 KEEP 份，默认 14）
# 恢复：gunzip < backups/db-<时间戳>.sql.gz | sudo -u postgres psql gold_desire
# =============================================================================
set -euo pipefail
KEEP="${BACKUP_KEEP:-14}"
OUT_DIR="$HOME/backups/gold-desire"
mkdir -p "$OUT_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${OUT_DIR}/db-${STAMP}.sql.gz"
sudo -u postgres pg_dump gold_desire --no-owner --no-privileges | gzip > "$OUT"
if [ ! -s "$OUT" ]; then echo "备份文件为空，疑似 pg_dump 失败" >&2; exit 1; fi
ls -1 "${OUT_DIR}"/db-*.sql.gz 2>/dev/null | head -n -"$KEEP" | while read -r old; do rm -f "$old"; done
echo "备份完成：$OUT（$(du -h "$OUT" | cut -f1)）"
