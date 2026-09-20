/**
 * 情绪走势区（近 N 个交易日）：三张图的编排容器。
 *
 * 加载 / 错误 / 空态在容器层统一处理一次，避免三个图表各自闪三份骨架；
 * 数据就绪后按 1) 温度+溢价双轴 2) 涨跌停+炸板 3) 上涨/下跌家数 依次渲染。
 */

import { AlertTriangle } from 'lucide-react';
import { ErrorState, EmptyState, LoadingState } from '@/components/common/StateViews';
import type { SentimentHistoryResponse } from '@/types/market';
import { toHistoryRows } from '../lib/historyRows';
import { BreadthChart } from './BreadthChart';
import { LimitCountsChart } from './LimitCountsChart';
import { TemperaturePremiumChart } from './TemperaturePremiumChart';

export interface SentimentHistoryChartProps {
  data: SentimentHistoryResponse | undefined;
  loading: boolean;
  error: unknown;
  onRetry?: () => void;
}

export function SentimentHistoryChart({
  data,
  loading,
  error,
  onRetry,
}: SentimentHistoryChartProps) {
  const rows = toHistoryRows(data?.items ?? []);
  const days = data?.days ?? 20;

  return (
    <section data-testid="sentiment-history" className="space-y-4" aria-label="情绪走势">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">近 {days} 个交易日情绪走势</h2>
        {rows.length > 0 && (
          <span className="text-muted-foreground text-xs">
            {rows[0].fullDate} ~ {rows[rows.length - 1].fullDate}
          </span>
        )}
      </div>

      {loading ? (
        <LoadingState title="加载情绪历史…" />
      ) : error ? (
        <ErrorState error={error} onRetry={onRetry} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="暂无情绪历史"
          description="库中尚无历史情绪记录，等待采集任务产出。"
        />
      ) : (
        <>
          <TemperaturePremiumChart rows={rows} />
          <LimitCountsChart rows={rows} />
          <BreadthChart rows={rows} />
        </>
      )}

      {data?.stale && (
        <p className="text-amber-400/90 flex items-center gap-1.5 text-xs">
          <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
          历史序列含陈旧数据（库中最新 {data.data_date ?? '--'}）。
        </p>
      )}
    </section>
  );
}
