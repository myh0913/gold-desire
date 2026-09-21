/**
 * 复盘页：某交易日的市场情绪 / 池型统计 / 连板天梯 / 建议回溯。
 *
 * 数据：`GET /api/review?date=`（派生聚合视图，缺省最新）+ `/api/review/dates` 下拉。
 * 建议回溯口径（后端 ReviewService，日线近似）：
 * - `closed` 持到可卖日收盘了结；`stopped` 可卖日低点触及止损价；`pending` 可卖日未入库。
 */

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { DataTable, type DataTableColumn } from '@/components/common/DataTable';
import { EmptyState, ErrorState, LoadingState } from '@/components/common/StateViews';
import { useChannelRefresh } from '@/hooks/useChannelRefresh';
import { reportApi } from '@/lib/api';
import { formatAmount, formatNumber, formatPct, formatPercentPlain, priceToneClass } from '@/lib/format';
import type { ReviewAdviceOutcomeOut, ReviewPoolTopOut, ReviewResponse } from '@/types/review';

const STATUS_LABEL: Record<string, string> = {
  pending: '待评估',
  stopped: '止损离场',
  closed: '已了结',
};

const POOL_LABEL: Record<string, string> = {
  limit_up: '涨停池',
  broken: '炸板池',
};

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="space-y-0.5">
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className={`text-lg font-semibold tabular-nums ${tone ?? ''}`}>{value}</p>
    </div>
  );
}

function SentimentPanel({ review }: { review: ReviewResponse }) {
  const sentiment = review.sentiment;
  if (!sentiment) {
    return <EmptyState title="该日无情绪数据" description="情绪指标在盘后采集入库后生成。" />;
  }
  const delta = review.temperature_delta;
  return (
    <CardContent className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-4xl font-bold tabular-nums">
          {formatNumber(sentiment.temperature, 1)}
        </span>
        <span className="text-muted-foreground text-sm">温度</span>
        {sentiment.stage && <Badge variant="secondary">{sentiment.stage}</Badge>}
        {delta != null && (
          <span className={`text-sm tabular-nums ${priceToneClass(delta)}`}>
            较前日 {delta > 0 ? '+' : ''}
            {delta.toFixed(1)}
            {review.prev_trade_date ? `（${review.prev_trade_date}）` : ''}
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="涨停 / 跌停" value={`${sentiment.limit_up_count} / ${sentiment.limit_down_count}`} />
        <Stat
          label="炸板数 / 炸板率"
          value={`${sentiment.broken_board_count} / ${formatPercentPlain(sentiment.broken_rate * 100)}`}
        />
        <Stat label="上涨 / 下跌" value={`${formatNumber(sentiment.up_count)} / ${formatNumber(sentiment.down_count)}`} />
        <Stat
          label="最高连板"
          value={sentiment.max_continue_days != null ? `${sentiment.max_continue_days} 板` : '--'}
        />
        <Stat
          label="昨涨停溢价"
          value={formatPct(sentiment.premium_rate * 100)}
          tone={priceToneClass(sentiment.premium_rate)}
        />
      </div>
    </CardContent>
  );
}

const ladderColumns: DataTableColumn<ReviewPoolTopOut>[] = [
  { key: 'code', header: '代码', render: (row) => <span className="tabular-nums">{row.code}</span> },
  { key: 'name', header: '名称', render: (row) => row.name },
  {
    key: 'continue_days',
    header: '连板',
    align: 'right',
    render: (row) => (
      <Badge variant={row.continue_days >= 4 ? 'up' : 'secondary'}>{row.continue_days} 板</Badge>
    ),
  },
  { key: 'limit_up_time', header: '首封时间', render: (row) => row.limit_up_time ?? '--' },
  {
    key: 'seal',
    header: '封板金额',
    align: 'right',
    render: (row) => (row.seal_amount_yuan != null ? formatAmount(row.seal_amount_yuan) : '--'),
  },
  {
    key: 'turnover',
    header: '换手率',
    align: 'right',
    render: (row) =>
      row.turnover_rate != null ? formatPercentPlain(row.turnover_rate * 100) : '--',
  },
];

const adviceColumns: DataTableColumn<ReviewAdviceOutcomeOut>[] = [
  {
    key: 'code',
    header: '标的',
    render: (row) => (
      <span>
        {row.name ?? ''} <span className="text-muted-foreground text-xs">{row.code}</span>
      </span>
    ),
  },
  {
    key: 'path',
    header: '路次',
    render: (row) => (
      <Badge variant={row.path_id === 'S2' ? 'default' : 'secondary'}>
        {row.path_id}
        {row.path_label ? ` · ${row.path_label}` : ''}
      </Badge>
    ),
  },
  {
    key: 'buy',
    header: '买点',
    render: (row) =>
      row.buy_day ? (
        <span className="tabular-nums">
          {row.buy_day} @ {row.buy_price != null ? row.buy_price.toFixed(2) : '--'}
        </span>
      ) : (
        '--'
      ),
  },
  {
    key: 'position',
    header: '仓位',
    align: 'right',
    render: (row) => (row.position != null ? formatPercentPlain(row.position * 100, 0) : '--'),
  },
  {
    key: 'status',
    header: '状态',
    render: (row) => (
      <Badge variant={row.status === 'closed' ? 'secondary' : row.status === 'stopped' ? 'down' : 'outline'}>
        {STATUS_LABEL[row.status] ?? row.status}
      </Badge>
    ),
  },
  {
    key: 'sell',
    header: '卖出',
    render: (row) =>
      row.sell_date ? (
        <span className="tabular-nums">
          {row.sell_date} @ {row.sell_price != null ? row.sell_price.toFixed(2) : '--'}
        </span>
      ) : (
        '--'
      ),
  },
  {
    key: 'return',
    header: '收益',
    align: 'right',
    render: (row) => (
      <span className={`tabular-nums ${priceToneClass(row.return_pct)}`}>
        {row.return_pct != null ? formatPct(row.return_pct * 100) : '--'}
      </span>
    ),
  },
];

export default function ReviewPage() {
  useChannelRefresh(['advice'], ['report', 'review']);
  const [date, setDate] = useState('');
  const datesQuery = useQuery({
    queryKey: ['report', 'review', 'dates'],
    queryFn: ({ signal }) => reportApi.reviewDates(signal),
  });
  const reviewQuery = useQuery({
    queryKey: ['report', 'review', date],
    queryFn: ({ signal }) => reportApi.review(date ? { date } : {}, signal),
  });
  const review = useMemo(() => reviewQuery.data, [reviewQuery.data]);
  const dates = datesQuery.data?.dates ?? [];
  const stats = review?.advice_stats;

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">复盘</h1>
        <div className="flex items-center gap-2">
          <Label htmlFor="review-date" className="text-sm">
            交易日
          </Label>
          <Select
            id="review-date"
            value={date}
            onChange={(event) => setDate(event.target.value)}
            className="w-40"
          >
            <option value="">最新（{review?.trade_date ?? '…'}）</option>
            {dates.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </Select>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void reviewQuery.refetch()}
            disabled={reviewQuery.isFetching}
          >
            刷新
          </Button>
        </div>
      </div>

      {reviewQuery.isLoading && <LoadingState className="min-h-48" />}
      {reviewQuery.isError && (
        <ErrorState error={reviewQuery.error} onRetry={() => void reviewQuery.refetch()} />
      )}

      {review && (
        <>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">市场情绪</CardTitle>
            </CardHeader>
            <SentimentPanel review={review} />
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">池型与天梯</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap gap-2">
                {Object.entries(review.pool_counts).map(([poolType, count]) => (
                  <Badge key={poolType} variant="outline">
                    {POOL_LABEL[poolType] ?? poolType} {count}
                  </Badge>
                ))}
                {Object.keys(review.pool_counts).length === 0 && (
                  <span className="text-muted-foreground text-sm">该日无池型数据</span>
                )}
              </div>
              <DataTable
                columns={ladderColumns}
                rows={review.top_ladder}
                rowKey={(row) => row.code}
                emptyTitle="无涨停池头部数据"
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">建议回溯</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {stats && (
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
                  <Stat label="建议总数" value={String(stats.total)} />
                  <Stat label="已了结" value={String(stats.settled)} />
                  <Stat label="待评估" value={String(stats.pending)} />
                  <Stat
                    label="胜率"
                    value={stats.win_rate != null ? formatPercentPlain(stats.win_rate * 100, 1) : '--'}
                  />
                  <Stat
                    label="平均收益"
                    value={stats.avg_return_pct != null ? formatPct(stats.avg_return_pct * 100) : '--'}
                    tone={priceToneClass(stats.avg_return_pct)}
                  />
                </div>
              )}
              <DataTable
                columns={adviceColumns}
                rows={review.advices}
                rowKey={(row) => `${row.path_id}-${row.code}-${row.buy_day ?? 'none'}`}
                emptyTitle="该日无建议记录"
                emptyDescription="策略产出建议后（盘后自动判定），这里逐条回溯收益与离场方式。"
              />
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
