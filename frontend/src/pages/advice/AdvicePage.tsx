/**
 * 量化选股页（原「今日建议」）：盘后建池候选 + 策略结构化建议卡片，WS 实时刷新。
 *
 * - 盘后建池（次日参考）：`GET /api/dragon/pool`（缺省最近有候选的交易日）——
 *   Phase.POOL 盘后落库的候选标的（用户 2026-09-22 决策）；
 * - 实时建议：`GET /api/advice?date=`（缺省最近有报告的交易日）+ 日期下拉；
 * - 实时：订阅 WS `advice` / `pool` 频道，收到推送即失效查询重拉；
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
import { useCycleQuery } from '@/lib/queries/market';
import { formatPercentPlain } from '@/lib/format';
import { getWsClient } from '@/lib/ws';
import type {
  AdviceGate,
  AdviceReportOut,
  DragonAdvicePayload,
  DragonPoolItemOut,
} from '@/types/report';

/** 报告域查询键（WS 推送失效用）。 */
export const ADVICE_KEYS = {
  list: ['report', 'advice'] as const,
  pool: ['report', 'dragon_pool'] as const,
};

/** 订阅 WS `advice` / `pool` / `cycle` 频道：建议、建池候选或情绪状态变化即失效对应查询。 */
function useStrategyStream(): void {
  const client = getWsClient();
  const queryClient = useQueryClient();
  useEffect(() => {
    client.connect();
    client.subscribe(['advice', 'pool', 'cycle']);
    const unsubscribe = client.onMessage((message) => {
      if (message.type === 'advice') {
        void queryClient.invalidateQueries({ queryKey: ADVICE_KEYS.list });
      }
      if (message.type === 'pool') {
        void queryClient.invalidateQueries({ queryKey: ADVICE_KEYS.pool });
      }
      if (message.type === 'cycle') {
        // 盘中情绪状态变化（T-0004）：横幅实时刷新；禁买态下建议被门控不推送
        void queryClient.invalidateQueries({ queryKey: ['market', 'cycle'] });
      }
    });
    return () => {
      unsubscribe();
      client.unsubscribe(['advice', 'pool', 'cycle']);
    };
  }, [client, queryClient]);
}

/** 周期态 → 横幅文案。禁买态（冰点/退潮）明示「满足条件也不推送」。 */
const GATE_BLOCKED_STATES = new Set(['冰点', '退潮']);

function CycleGateBanner() {
  const { data } = useCycleQuery();
  const cycle = data?.item ?? null;
  if (!cycle) return null;
  const blocked = GATE_BLOCKED_STATES.has(cycle.state);
  return (
    <div
      data-testid="cycle-gate-banner"
      className={`rounded-md border p-3 text-sm ${
        blocked
          ? 'border-destructive/40 bg-destructive/10 text-destructive'
          : 'bg-muted/40 text-muted-foreground'
      }`}
    >
      当前情绪周期：<span className="font-semibold">{cycle.state}</span>
      {cycle.position_factor !== null && cycle.position_factor !== undefined && (
        <span className="ml-2">仓位系数 {Math.round(cycle.position_factor * 100)}%</span>
      )}
      {blocked ? (
        <span className="ml-2 font-medium">
          —— 禁买期：即使策略满足条件，买点建议也不会推送
        </span>
      ) : (
        <span className="ml-2">—— 交易窗口正常，策略建议实时推送</span>
      )}
    </div>
  );
}

function asPayload(raw: Record<string, unknown>): DragonAdvicePayload {
  return raw as unknown as DragonAdvicePayload;
}

function pathBadgeVariant(
  pathId: string,
): 'default' | 'secondary' | 'up' | 'outline' {
  if (pathId === 'S2' || pathId === 'dip') return 'default';
  if (pathId === 'S4' || pathId === 'A') return 'secondary';
  if (pathId === 'auction') return 'up';
  return 'outline';
}

/** 盘后建池候选（次日参考）区块。 */
function DragonPoolSection() {
  const poolQuery = useQuery({
    queryKey: [...ADVICE_KEYS.pool],
    queryFn: ({ signal }) => reportApi.dragonPool({}, signal),
  });
  const items = useMemo(() => poolQuery.data?.items ?? [], [poolQuery.data]);

  return (
    <section className="space-y-2" data-testid="dragon-pool-section">
      <div className="flex items-center gap-2">
        <h2 className="text-base font-semibold">盘后建池（次日参考）</h2>
        {poolQuery.data?.trade_date && (
          <span className="text-muted-foreground text-xs">
            建池日 {poolQuery.data.trade_date}
          </span>
        )}
      </div>

      {poolQuery.isLoading && <LoadingState className="min-h-24" />}
      {poolQuery.isError && (
        <ErrorState error={poolQuery.error} onRetry={() => void poolQuery.refetch()} />
      )}
      {poolQuery.isSuccess && items.length === 0 && (
        <EmptyState
          title="今日暂无建池候选"
          description="策略在收盘后自动识别龙回头结构；识别到候选会经 WS 实时推送并出现在这里。"
        />
      )}
      {items.length > 0 && (
        <div className="space-y-2">
          {items.map((item) => (
            <PoolCandidateRow key={`${item.code}-${item.d_date}`} item={item} />
          ))}
        </div>
      )}
    </section>
  );
}

function PoolCandidateRow({ item }: { item: DragonPoolItemOut }) {
  return (
    <Card data-testid="pool-candidate">
      <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-2 p-3">
        <div className="min-w-32">
          <span className="text-sm font-semibold">{item.name ?? item.code}</span>
          <span className="text-muted-foreground ml-2 text-xs">{item.code}</span>
        </div>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
          <div>
            <dt className="text-muted-foreground">首阴日</dt>
            <dd>{item.d_date}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">连板数</dt>
            <dd>{item.boards}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">分时形态</dt>
            <dd>{item.shape_label ?? '--'}</dd>
          </div>
        </dl>
      </CardContent>
    </Card>
  );
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
  useStrategyStream();
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
    <div className="space-y-6 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">量化选股</h1>
        <div className="flex items-center gap-2">
          <Label htmlFor="advice-date" className="text-sm">
            建议交易日
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

      <CycleGateBanner />

      <DragonPoolSection />

      <section className="space-y-2">
        <h2 className="text-base font-semibold">实时建议</h2>

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
      </section>
    </div>
  );
}
