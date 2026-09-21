/**
 * 回测页：任务列表 + 单任务 A/B/C 三段报告；admin 可触发新回测（同步执行）。
 */

import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { DataTable, type DataTableColumn } from '@/components/common/DataTable';
import { reportApi } from '@/lib/api';
import { usePageAccess } from '@/hooks/useAuth';
import { daysAgoSh, todaySh } from '@/lib/time';
import type { BacktestRunOut } from '@/types/report';

const STATUS_VARIANT: Record<string, 'secondary' | 'up' | 'down' | 'outline'> = {
  succeeded: 'up',
  running: 'secondary',
  failed: 'down',
};

function segmentsOf(report: Record<string, unknown> | null): Array<Record<string, unknown>> {
  if (!report) return [];
  const segments = (report as { segments?: unknown }).segments;
  return Array.isArray(segments) ? (segments as Array<Record<string, unknown>>) : [];
}

const runColumns = (onSelect: (run: BacktestRunOut) => void): DataTableColumn<BacktestRunOut>[] => [
  {
    key: 'run_id',
    header: '任务',
    render: (row) => (
      <button
        type="button"
        className="text-primary text-xs underline-offset-2 hover:underline"
        onClick={() => onSelect(row)}
      >
        {row.run_id.slice(0, 12)}…
      </button>
    ),
  },
  {
    key: 'status',
    header: '状态',
    render: (row) => (
      <Badge variant={STATUS_VARIANT[row.status] ?? 'outline'}>{row.status}</Badge>
    ),
  },
  {
    key: 'range',
    header: '区间',
    render: (row) => (
      <span className="tabular-nums text-xs">
        {row.start_date} ~ {row.end_date}
      </span>
    ),
  },
  { key: 'strategies', header: '策略', render: (row) => row.strategies.join(', ') },
  {
    key: 'created_by',
    header: '发起人',
    render: (row) => row.created_by ?? '--',
  },
];

export default function BacktestPage() {
  const { isAdmin } = usePageAccess();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<BacktestRunOut | null>(null);
  const [start, setStart] = useState(daysAgoSh(365));
  const [end, setEnd] = useState(todaySh());
  const [triggering, setTriggering] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runsQuery = useQuery({
    queryKey: ['report', 'backtest', 'runs'],
    queryFn: ({ signal }) => reportApi.backtestRuns(signal),
    // 有 running 任务时每 2s 轮询，全部终态后停止（异步回测触发后靠此刷新）。
    refetchInterval: (query) =>
      (query.state.data?.items ?? []).some((run) => run.status === 'running') ? 2000 : false,
  });

  const active = selected
    ? (runsQuery.data?.items ?? []).find((run) => run.run_id === selected.run_id) ?? selected
    : null;

  async function trigger(): Promise<void> {
    setTriggering(true);
    setError(null);
    try {
      const run = await reportApi.triggerBacktest({ start, end });
      setSelected(run);
      await queryClient.invalidateQueries({ queryKey: ['report', 'backtest'] });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '触发失败');
    } finally {
      setTriggering(false);
    }
  }

  return (
    <div className="space-y-4 p-4">
      <h1 className="text-xl font-semibold">回测</h1>

      {isAdmin && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">触发回测（龙回头 · A/B/C 三段）</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <Label htmlFor="bt-start" className="text-xs">
                起始日
              </Label>
              <Input
                id="bt-start"
                type="date"
                value={start}
                onChange={(event) => setStart(event.target.value)}
                className="w-40"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="bt-end" className="text-xs">
                结束日
              </Label>
              <Input
                id="bt-end"
                type="date"
                value={end}
                onChange={(event) => setEnd(event.target.value)}
                className="w-40"
              />
            </div>
            <Button onClick={() => void trigger()} disabled={triggering || start >= end}>
              {triggering ? '回测中…' : '运行回测'}
            </Button>
            {error && <p className="text-stock-down text-xs">{error}</p>}
          </CardContent>
        </Card>
      )}

      <DataTable
        columns={runColumns(setSelected)}
        rows={runsQuery.data?.items}
        rowKey={(row) => row.run_id}
        loading={runsQuery.isLoading}
        error={runsQuery.error}
        onRetry={() => void runsQuery.refetch()}
        emptyTitle="暂无回测任务"
      />

      {active && (
        <Card data-testid="backtest-detail">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">
              {active.run_id}
              <Badge variant={STATUS_VARIANT[active.status] ?? 'outline'} className="ml-2">
                {active.status}
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <p className="text-muted-foreground text-xs">
              区间 {active.start_date} ~ {active.end_date} · 参数{' '}
              {JSON.stringify(active.params)}
            </p>
            {active.error && <p className="text-stock-down text-xs">{active.error}</p>}
            {segmentsOf(active.report).length > 0 ? (
              <div className="overflow-x-auto rounded-md border">
                <table className="w-full border-collapse text-xs">
                  <tbody>
                    {segmentsOf(active.report).map((segment, index) => (
                      <tr key={String(segment.name ?? index)} className="border-t first:border-t-0">
                        <th className="bg-muted/40 px-3 py-2 text-left font-medium">
                          {String(segment.name ?? `段 ${index + 1}`)}
                        </th>
                        <td className="px-3 py-2">
                          <pre className="whitespace-pre-wrap break-all">
                            {JSON.stringify(segment, null, 2)}
                          </pre>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-muted-foreground text-xs">
                {active.status === 'succeeded' ? '无分段报告' : '任务未完成，暂无报告'}
              </p>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
