/**
 * 总览页（Task 15.5）：情绪温度计 + 统计卡 + 观察要点 + 情绪走势。
 *
 * 数据：`GET /api/sentiment`（含 `stale` 新鲜度标记）与 `GET /api/sentiment/history?days=20`；
 * 实时：订阅 WS `sentiment` / `pool` 频道，收到推送即失效查询重拉。
 */

import { Card, CardContent } from '@/components/ui/card';
import { AlertBanner } from '@/components/common/AlertBanner';
import { ConnectionStatus } from '@/components/common/ConnectionStatus';
import { EmptyState, ErrorState, LoadingState } from '@/components/common/StateViews';
import { useCycleQuery, useSentimentHistoryQuery, useSentimentQuery } from '@/lib/queries/market';
import { fmtDate } from '@/lib/time';
import { CycleStatusCard } from './components/CycleStatusCard';
import { ObservationNotes } from './components/ObservationNotes';
import { YesterdayReviewCard } from './components/YesterdayReviewCard';
import { SentimentGauge } from './components/SentimentGauge';
import { SentimentHistoryChart } from './components/SentimentHistoryChart';
import { StatCards } from './components/StatCards';
import { useSentimentStream } from './hooks/useSentimentStream';

export default function OverviewPage() {
  const sentiment = useSentimentQuery();
  const history = useSentimentHistoryQuery(20);
  const cycle = useCycleQuery();
  const { alerts, dismissAlert } = useSentimentStream();

  const item = sentiment.data?.item ?? null;
  const stale = sentiment.data?.stale ?? false;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">总览</h1>
          <p className="text-muted-foreground text-sm">
            市场情绪、涨停结构与 20 日情绪走势（时间口径 Asia/Shanghai）。
          </p>
        </div>
        <ConnectionStatus />
      </header>

      {stale && (
        <AlertBanner
          variant="stale"
          message={`上游数据可能未及时更新，当前展示库中最新数据${
            sentiment.data?.data_date ? `（${fmtDate(sentiment.data.data_date)}）` : ''
          }。`}
        />
      )}

      {alerts.map((message, index) => (
        <AlertBanner
          key={`${message}-${index}`}
          variant="alert"
          message={message}
          autoHideMs={0}
          onDismiss={() => dismissAlert(index)}
        />
      ))}

      <CycleStatusCard cycle={cycle.data?.item ?? null} />

      <YesterdayReviewCard />

      {sentiment.isPending ? (
        <LoadingState title="加载情绪指标…" />
      ) : sentiment.error ? (
        <ErrorState error={sentiment.error} onRetry={() => void sentiment.refetch()} />
      ) : !item ? (
        <EmptyState
          title="暂无情绪数据"
          description="库中尚无该交易日的情绪指标，等待采集任务产出。"
        />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card>
              <CardContent className="p-4">
                <SentimentGauge
                  temperature={item.temperature}
                  stage={item.stage}
                  maxContinueDays={item.max_continue_days}
                  premiumRate={item.premium_rate}
                />
              </CardContent>
            </Card>

            <div className="flex flex-col gap-4 lg:col-span-2">
              <StatCards sentiment={item} />
              <ObservationNotes sentiment={item} />
            </div>
          </div>

          <SentimentHistoryChart
            data={history.data}
            loading={history.isPending}
            error={history.error}
            onRetry={() => void history.refetch()}
          />
        </>
      )}
    </div>
  );
}
