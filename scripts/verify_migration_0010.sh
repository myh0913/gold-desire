#!/bin/bash
# 验证迁移 0010 的表结构（只读）
set -e
cd /home/ubuntu/apps/gold-desire
DBURL=$(grep -E "^DATABASE_URL=" .env | cut -d= -f2-)
export PGUSER=$(echo "$DBURL" | sed -E 's|.*://([^:]+):.*|\1|')
export PGPASSWORD=$(echo "$DBURL" | sed -E 's|.*://[^:]+:([^@]+)@.*|\1|')
Q() { psql -h 127.0.0.1 -U "$PGUSER" -d gold_desire -Atc "$1"; }
echo "max_seal_amount_yuan in limit_up_pool: $(Q "SELECT count(*) FROM information_schema.columns WHERE table_name='limit_up_pool' AND column_name='max_seal_amount_yuan';")"
echo "selected_at in theme_stocks (expect 0): $(Q "SELECT count(*) FROM information_schema.columns WHERE table_name='theme_stocks' AND column_name='selected_at';")"
echo "alembic version: $(Q "SELECT version_num FROM alembic_version;")"
