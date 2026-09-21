/**
 * 今日建议页：策略结构化建议卡片 + WS 实时刷新。
 *
 * - 数据：`GET /api/advice?date=`（缺省最近有报告的交易日）+ 日期下拉（`/api/advice/dates`）；
 * - 实时：订阅 WS `advice` 频道，收到推送即失效查询重拉（策略盘后落库后自动出现）；
 * - 卡片：路次（S2/S4）/ 标的 / 买点 / 建议仓位 / 止损价 / 卖出时点 / 硬门槛与加分项明细。
 */

import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { EmptyState, ErrorState, LoadingState } from '@/components/common/StateViews';
import { StaleNotice } from '@/components/common/StaleNotice';
import { reportApi } from '@/lib/api';
import { formatPercentPlain } from '@/lib/format';
import { getWsClient } from '@/lib/ws';
import type { AdviceGate, AdviceReportOut, DragonAdvicePayload } from '@/types/report';

/** 报告域查询键（WS 推送失效用）。 */
export const ADVICE_KEYS = {
  list: ['report', 'advice'] as const,
};

/** 订阅 WS `advice` 频道：新建议落库即失效列表查询。 */
function useAdviceStream(): void {
  const client = getWsClient();
  const queryClient = useQueryClient();
  useEffect(() => {
    client.connect();
    client.subscribe(['advice']);
    const unsubscribe = client.onMessage((message) => {
      if (message.type === 'advice') {
        void queryClient.invalidateQueries({ queryKey: ADVICE_KEYS.list });
      }
    });
    return () => {
      unsubscribe();
      client.unsubscribe(['advice']);
    };
  }, [client, queryClient]);
}

function asPayload(raw: Record<string, unknown>): DragonAdvicePayload {
  return raw as unknown as DragonAdvicePayload;
}

function pathBadgeVariant(pathId: string): 'default' | 'secondary' | 'outline' {
  if (pathId === 'S2') return 'default';
  if (pathId === 'S4') return 'secondary';
  return 'outline';
}

function AdviceCard({ report }: { report: AdviceReportOut }) {
  const advice = asPayload(report.payload);
  const gates: AdviceGate[] = advice.gates ?? [];
  return (
    <Card data-testid="advice-card">
      <CardContent className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={pathBadgeVariant(advice.path_id)}>{advice.path_id}</Badge>
          <span className="text-base font-semibold">{advice.name ?? advice.code}</span>
          <span className="text-muted-foreground text-xs">{advice.code}</span>
          {advice.bonus_score != null && (
            <Badge variant="outline">加分 {advice.bonus_score}</Badge>
          )}
        </div>

        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-muted-foreground text-xs">买点</dt>
            <dd>
              {advice.buy_day ?? '--'} 开盘 {advice.buy_price != null ? advice.buy_price.toFixed(2) : '--'}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">建议仓位</dt>
            <dd>{advice.position != null ? formatPercentPlain(advice.position * 100, 0) : '--'}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">止损价</dt>
            <dd>{advice.stop_loss_price != null ? advice.stop_loss_price.toFixed(2) : '--'}</dd>
          </div>
        </dl>

        {advice.sell_timing && (
          <p className="text-muted-foreground text-xs">卖出：{advice.sell_timing}</p>
        )}

        <div className="space-y-1">
          <p className="text-xs font-medium">硬门槛</p>
          <ul className="space-y-0.5 text-xs">
            {gates.map((gate) => (
              <li key={gate.factor_id} className="flex items-center gap-2">
                <span className={gate.passed ? 'text-stock-up' : 'text-stock-down'}>
                  {gate.passed ? '✓' : '✗'}
                </span>
                <span>{gate.label}</span>
                {gate.detail && <span className="text-muted-foreground">（{gate.detail}）</span>}
              </li>
            ))}
          </ul>
        </div>

        {(advice.bonus?.length ?? 0) > 0 && (
          <div className="space-y-1">
            <p className="text-xs font-medium">加分项</p>
            <div className="flex flex-wrap gap-1">
              {advice.bonus.map((item) => (
                <Badge
                  key={item.factor_id}
                  variant={item.satisfied ? 'secondary' : 'outline'}
                  className="text-[11px]"
                >
                  {item.satisfied ? '＋' : '－'}
                  {item.label}
                </Badge>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function AdvicePage() {
  useAdviceStream();
  const [date, setDate] = useState<string>('');

  const datesQuery = useQuery({
    queryKey: ['report', 'advice', 'dates'],
    queryFn: ({ signal }) => reportApi.adviceDates(signal),
  });

  const adviceQuery = useQuery({
    queryKey: [...ADVICE_KEYS.list, date],
    queryFn: ({ signal }) => reportApi.advice(date ? { date } : {}, signal),
  });

  const dates = useMemo(() => datesQuery.data?.dates ?? [], [datesQuery.data]);
  const reports = useMemo(
    () =>
      (adviceQuery.data?.items ?? []).filter((item) => item.kind === 'advice'),
    [adviceQuery.data],
  );

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">今日建议</h1>
        <div className="flex items-center gap-2">
          <Label htmlFor="advice-date" className="text-sm">
            交易日
          </Label>
          <Select
            id="advice-date"
            value={date}
            onChange={(event) => setDate(event.target.value)}
            className="w-40"
          >
            <option value="">最新（{adviceQuery.data?.trade_date ?? '…'}）</option>
            {dates.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </Select>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void adviceQuery.refetch()}
            disabled={adviceQuery.isFetching}
          >
            刷新
          </Button>
        </div>
      </div>

      {adviceQuery.data?.stale && (
        <StaleNotice
          stale={adviceQuery.data.stale}
          dataDate={adviceQuery.data.data_date}
          label="建议数据"
        />
      )}

      {adviceQuery.isLoading && <LoadingState className="min-h-48" />}
      {adviceQuery.isError && (
        <ErrorState error={adviceQuery.error} onRetry={() => void adviceQuery.refetch()} />
      )}
      {adviceQuery.isSuccess && reports.length === 0 && (
        <EmptyState
          title="暂无建议"
          description="策略在盘后数据齐备时自动判定；有新建议会经 WS 实时推送并出现在这里。"
        />
      )}
      {reports.length > 0 && (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {reports.map((report) => (
            <AdviceCard key={`${report.ran_at}-${String(report.payload.code ?? '')}`} report={report} />
          ))}
        </div>
      )}
    </div>
  );
}
