#!/bin/bash
# 只读 DB 证据查询（任务3）
set -e
cd /home/ubuntu/apps/gold-desire
DBURL=$(grep -E "^DATABASE_URL=" .env | cut -d= -f2-)
export PGUSER=$(echo "$DBURL" | sed -E 's|.*://([^:]+):.*|\1|')
export PGDB=$(echo "$DBURL" | sed -E 's|.*/([^/?]+)(\?.*)?$|\1|')
export PGPASSWORD=$(echo "$DBURL" | sed -E 's|.*://[^:]+:([^@]+)@.*|\1|')
Q() { psql -h 127.0.0.1 -d "$PGDB" -Atc "$1"; }
Q "SELECT 'limit_up_pool total=' || count(*) || ' seal_null=' || count(*) FILTER (WHERE seal_amount_yuan IS NULL) || ' amount_null=' || count(*) FILTER (WHERE amount_yuan IS NULL) || ' time_null=' || count(*) FILTER (WHERE limit_up_time IS NULL) FROM limit_up_pool;"
Q "SELECT 'stocks total=' || count(*) || ' list_date_present=' || count(list_date) FROM stocks;"
Q "SELECT 'theme_stocks total=' || count(*) || ' selected_at_present=' || count(selected_at) FROM theme_stocks;"
Q "SELECT 'raw_responses total=' || count(*) || ' elapsed_present=' || count(elapsed_ms) || ' http_status_present=' || count(http_status) FROM raw_responses;"
Q "SELECT 'ladder total=' || count(*) || ' first_seal_present=' || count(first_seal_time) FROM ladder;"
Q "SELECT 'minute_bars per-day: ' || string_agg(trade_date || ':' || c, ', ' ORDER BY trade_date DESC) FROM (SELECT trade_date, count(DISTINCT code) c FROM minute_bars GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5) t;"
