/**
 * 连板天梯页：区间连板个股，按交易日 + 连板数排序，分页展示。
 */

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { DataTable, type DataTableColumn } from '@/components/common/DataTable';
import { marketApi } from '@/lib/api';
import type { LadderRowOut } from '@/types/market';

const PAGE_SIZE = 20;

const columns: DataTableColumn<LadderRowOut>[] = [
  { key: 'date', header: '交易日', render: (row) => row.trade_date },
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
  { key: 'seal', header: '首封时间', render: (row) => row.first_seal_time ?? '--' },
];

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export default function LadderPage() {
  const datesQuery = useQuery({
    queryKey: ['market', 'ladder', 'dates'],
    queryFn: ({ signal }) => marketApi.ladderDates(signal),
  });
  const dates = useMemo(() => datesQuery.data?.dates ?? [], [datesQuery.data]);

  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [minDays, setMinDays] = useState(2);
  const [page, setPage] = useState(1);

  const effectiveStart = start || dates[dates.length - 1] || isoDaysAgo(30);
  const effectiveEnd = end || dates[0] || isoDaysAgo(0);

  const ladderQuery = useQuery({
    queryKey: ['market', 'ladder', effectiveStart, effectiveEnd, minDays, page],
    queryFn: ({ signal }) =>
      marketApi.ladder(
        { start: effectiveStart, end: effectiveEnd, min_continue_days: minDays, page, page_size: PAGE_SIZE },
        signal,
      ),
    enabled: Boolean(effectiveStart && effectiveEnd),
  });

  return (
    <div className="space-y-4 p-4">
      <h1 className="text-xl font-semibold">连板天梯</h1>

      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="ladder-start" className="text-xs">
            起始日
          </Label>
          <Input
            id="ladder-start"
            type="date"
            value={start}
            onChange={(event) => {
              setStart(event.target.value);
              setPage(1);
            }}
            className="w-40"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="ladder-end" className="text-xs">
            结束日
          </Label>
          <Input
            id="ladder-end"
            type="date"
            value={end}
            onChange={(event) => {
              setEnd(event.target.value);
              setPage(1);
            }}
            className="w-40"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="ladder-min" className="text-xs">
            最低连板
          </Label>
          <Input
            id="ladder-min"
            type="number"
            min={1}
            max={20}
            value={minDays}
            onChange={(event) => {
              setMinDays(Math.max(1, Number(event.target.value) || 1));
              setPage(1);
            }}
            className="w-24"
          />
        </div>
        <p className="text-muted-foreground text-xs">
          实际区间：{effectiveStart} ~ {effectiveEnd}
        </p>
      </div>

      <DataTable
        columns={columns}
        rows={ladderQuery.data?.items}
        rowKey={(row) => `${row.trade_date}-${row.code}`}
        loading={ladderQuery.isLoading}
        error={ladderQuery.error}
        onRetry={() => void ladderQuery.refetch()}
        emptyTitle="区间无天梯数据"
        page={page}
        pageSize={PAGE_SIZE}
        total={ladderQuery.data?.total}
        onPageChange={setPage}
      />
    </div>
  );
}
