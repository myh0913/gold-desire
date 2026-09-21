/**
 * 涨停池页：某交易日全部池型（涨停 / 炸板等），按池型分 Tab 展示。
 */

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { DataTable, type DataTableColumn } from '@/components/common/DataTable';
import { StaleNotice } from '@/components/common/StaleNotice';
import { useChannelRefresh } from '@/hooks/useChannelRefresh';
import { marketApi } from '@/lib/api';
import { formatAmount, formatPercentPlain } from '@/lib/format';
import type { PoolOut } from '@/types/market';

const POOL_LABEL: Record<string, string> = { limit_up: '涨停池', broken: '炸板池' };

const columns: DataTableColumn<PoolOut>[] = [
  { key: 'code', header: '代码', render: (row) => <span className="tabular-nums">{row.code}</span> },
  { key: 'name', header: '名称', render: (row) => row.name },
  {
    key: 'continue_days',
    header: '连板',
    align: 'right',
    render: (row) => (
      <Badge variant={row.continue_days >= 3 ? 'up' : 'secondary'}>{row.continue_days} 板</Badge>
    ),
  },
  { key: 'limit_up_time', header: '封板时间', render: (row) => row.limit_up_time ?? '--' },
  {
    key: 'seal_amount',
    header: '封板金额',
    align: 'right',
    render: (row) => (row.seal_amount_yuan != null ? formatAmount(row.seal_amount_yuan) : '--'),
  },
  {
    key: 'open_times',
    header: '开板次数',
    align: 'right',
    render: (row) => (row.open_times != null ? String(row.open_times) : '--'),
  },
  {
    key: 'turnover',
    header: '换手率',
    align: 'right',
    render: (row) =>
      row.turnover_rate != null ? formatPercentPlain(row.turnover_rate * 100) : '--',
  },
  {
    key: 'amount',
    header: '成交额',
    align: 'right',
    render: (row) => (row.amount_yuan != null ? formatAmount(row.amount_yuan) : '--'),
  },
  {
    key: 'market_cap',
    header: '市值',
    align: 'right',
    render: (row) => (row.market_cap_yuan != null ? formatAmount(row.market_cap_yuan) : '--'),
  },
];

export default function PoolsPage() {
  useChannelRefresh(['pool'], ['market', 'pools']);
  const [date, setDate] = useState('');
  const poolsQuery = useQuery({
    queryKey: ['market', 'pools', date],
    queryFn: ({ signal }) => marketApi.pools(date ? { date } : {}, signal),
  });
  const datesQuery = useQuery({
    queryKey: ['market', 'pools', 'dates'],
    queryFn: ({ signal }) => marketApi.ladderDates(signal),
  });

  const pools = poolsQuery.data?.pools ?? {};
  const poolTypes = useMemo(() => Object.keys(pools).sort(), [pools]);
  const [active, setActive] = useState('limit_up');
  const effective = poolTypes.includes(active) ? active : poolTypes[0];

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">涨停池</h1>
        <div className="flex items-center gap-2">
          <Label htmlFor="pools-date" className="text-sm">
            交易日
          </Label>
          <Select
            id="pools-date"
            value={date}
            onChange={(event) => setDate(event.target.value)}
            className="w-40"
          >
            <option value="">最新（{poolsQuery.data?.trade_date ?? '…'}）</option>
            {(datesQuery.data?.dates ?? []).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </Select>
        </div>
      </div>

      <StaleNotice
        stale={poolsQuery.data?.stale}
        dataDate={poolsQuery.data?.data_date}
        label="涨停池"
      />

      {poolTypes.length > 0 && (
        <Tabs value={effective ?? 'limit_up'} onValueChange={setActive}>
          <TabsList>
            {poolTypes.map((type) => (
              <TabsTrigger key={type} value={type}>
                {POOL_LABEL[type] ?? type}（{pools[type].length}）
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      )}

      <DataTable
        columns={columns}
        rows={effective ? pools[effective] : []}
        rowKey={(row) => `${row.code}-${row.pool_type}`}
        loading={poolsQuery.isLoading}
        error={poolsQuery.error}
        onRetry={() => void poolsQuery.refetch()}
        emptyTitle="该日无池型数据"
        emptyDescription="竞价 / 盘后窗口采集入库后展示。"
      />
    </div>
  );
}
