/**
 * 涨停池页：某交易日全部池型（涨停 / 炸板 / 昨涨停 / 强势 / 跌停 / 新股 / 次新）。
 *
 * 列口径与展开详情对齐参考实现（quant）：
 *
 * - 主表：股票 / 现价 / 涨幅 / 连板 / 换手 / 市值（**流通市值**）/ 封板时间 / 涨停原因 + 板块标签
 * - 展开详情：量比 / 封单比 / 总市值 / 流通市值 / 炸板次数 / 封板金额 / 最大封板金额 / 成交额
 *   + 封板时间线 + 涨停原因全文 + 关联板块
 *
 * 数据全部**读库**：`GET /api/pools` 一次取回全部池型（切 Tab 不再请求）；
 * 后端采集侧每 10 分钟轮询入库。实时：订阅 WS `pool` 频道，收到推送即失效重取。
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
import {
  formatAmount,
  formatNumber,
  formatPct,
  formatPercentPlain,
  priceToneClass,
  truncate,
} from '@/lib/format';
import { cn } from '@/lib/utils';
import type { PoolOut } from '@/types/market';
import { PoolTimeline } from './components/PoolTimeline';

/** 池型顺序与文案（与后端 `POOL_TYPES` 一致；顺序即 Tab 顺序）。 */
const POOL_TABS = [
  { key: 'limit_up', label: '涨停池' },
  { key: 'limit_up_broken', label: '炸板池' },
  { key: 'yesterday_limit_up', label: '昨涨停' },
  { key: 'super_stock', label: '强势股' },
  { key: 'limit_down', label: '跌停池' },
  { key: 'new_stock', label: '新股' },
  { key: 'nearly_new', label: '次新' },
] as const;

const DASH = <span className="text-muted-foreground">--</span>;

const columns: DataTableColumn<PoolOut>[] = [
  {
    key: 'stock',
    header: '股票',
    render: (row) => (
      <div className="min-w-[6rem]">
        <div className="font-medium">{row.name}</div>
        <div className="text-muted-foreground font-mono text-xs">{row.code}</div>
      </div>
    ),
  },
  {
    key: 'price',
    header: '现价',
    align: 'right',
    render: (row) => (
      <span className="tabular-nums">{row.price != null ? row.price.toFixed(2) : '--'}</span>
    ),
  },
  {
    key: 'change_pct',
    header: '涨幅',
    align: 'right',
    render: (row) => (
      <span className={cn('tabular-nums', priceToneClass(row.change_pct))}>
        {row.change_pct != null ? formatPct(row.change_pct * 100) : '--'}
      </span>
    ),
  },
  {
    key: 'continue_days',
    header: '连板',
    align: 'right',
    render: (row) =>
      row.continue_days > 0 ? (
        <Badge variant={row.continue_days >= 3 ? 'up' : 'secondary'}>{row.continue_days} 板</Badge>
      ) : (
        DASH
      ),
  },
  {
    key: 'turnover_rate',
    header: '换手',
    align: 'right',
    render: (row) => (
      <span className="tabular-nums">
        {row.turnover_rate != null ? formatPercentPlain(row.turnover_rate * 100) : '--'}
      </span>
    ),
  },
  {
    key: 'free_cap',
    header: '市值',
    align: 'right',
    render: (row) => (
      <span className="tabular-nums">
        {row.free_cap_yuan != null ? formatAmount(row.free_cap_yuan) : '--'}
      </span>
    ),
  },
  {
    key: 'limit_up_time',
    header: '封板时间',
    render: (row) => <span className="font-mono text-xs">{row.limit_up_time ?? '--'}</span>,
  },
  {
    key: 'reason',
    header: '涨停原因',
    className: 'max-w-[20rem]',
    render: (row) => (
      <div className="space-y-1">
        <div className="truncate text-xs" title={row.reason ?? ''}>
          {row.reason ? truncate(row.reason, 42) : '--'}
        </div>
        {row.plates && row.plates.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {row.plates.slice(0, 4).map((plate, index) => (
              <Badge
                key={plate.plate_id ?? `${plate.plate_name}-${index}`}
                variant="outline"
                className="px-1.5 py-0 text-[10px]"
              >
                {plate.plate_name}
              </Badge>
            ))}
          </div>
        )}
      </div>
    ),
  },
];

/** 展开详情：量化指标 + 封板时间线 + 涨停原因全文。 */
function PoolDetail({ row }: { row: PoolOut }) {
  const metrics: { label: string; value: string }[] = [
    { label: '量比', value: row.volume_bias_ratio?.toFixed(2) ?? '--' },
    { label: '封单比', value: row.seal_ratio != null ? formatPercentPlain(row.seal_ratio * 100, 4) : '--' },
    { label: '总市值', value: row.market_cap_yuan != null ? formatAmount(row.market_cap_yuan) : '--' },
    { label: '流通市值', value: row.free_cap_yuan != null ? formatAmount(row.free_cap_yuan) : '--' },
    { label: '炸板次数', value: row.open_times != null ? formatNumber(row.open_times) : '--' },
    { label: '封板金额', value: row.seal_amount_yuan != null ? formatAmount(row.seal_amount_yuan) : '--' },
    { label: '最大封板金额', value: row.max_seal_amount_yuan != null ? formatAmount(row.max_seal_amount_yuan) : '--' },
    { label: '成交额', value: row.amount_yuan != null ? formatAmount(row.amount_yuan) : '--' },
  ];

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="md:col-span-1">
          <div className="text-muted-foreground mb-2 text-xs">关键指标</div>
          <dl className="space-y-1 text-xs">
            {metrics.map((metric) => (
              <div key={metric.label} className="flex gap-2">
                <dt className="text-muted-foreground w-16 shrink-0">{metric.label}</dt>
                <dd className="font-mono">{metric.value}</dd>
              </div>
            ))}
          </dl>
        </div>
        <div className="md:col-span-2">
          <div className="text-muted-foreground mb-2 text-xs">封板时间线</div>
          <PoolTimeline items={row.timeline} />
        </div>
      </div>

      {row.reason && (
        <div className="bg-background rounded border p-2 text-xs leading-relaxed">
          <span className="text-muted-foreground mr-1">涨停原因：</span>
          {row.reason}
        </div>
      )}

      {row.plates && row.plates.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-muted-foreground text-xs">关联板块：</span>
          {row.plates.map((plate, index) => (
            <Badge
              key={plate.plate_id ?? `${plate.plate_name}-${index}`}
              variant="outline"
              className="text-[10px]"
            >
              {plate.plate_name}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}

export default function PoolsPage() {
  useChannelRefresh(['pool'], ['market', 'pools']);
  const [date, setDate] = useState('');
  const [active, setActive] = useState<string>('limit_up');

  const poolsQuery = useQuery({
    queryKey: ['market', 'pools', date],
    queryFn: ({ signal }) => marketApi.pools(date ? { date } : {}, signal),
  });
  const datesQuery = useQuery({
    queryKey: ['market', 'pools', 'dates'],
    queryFn: ({ signal }) => marketApi.ladderDates(signal),
  });

  const pools = poolsQuery.data?.pools ?? {};
  /** 固定顺序展示，仅保留后端确实返回的池型（避免空 Tab）。 */
  const visibleTabs = useMemo(
    () => POOL_TABS.filter((tab) => (pools[tab.key]?.length ?? 0) > 0),
    [pools],
  );
  const effective = visibleTabs.some((tab) => tab.key === active)
    ? active
    : (visibleTabs[0]?.key ?? 'limit_up');
  const rows = pools[effective] ?? [];

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

      {visibleTabs.length > 0 && (
        <Tabs value={effective} onValueChange={setActive}>
          <TabsList>
            {visibleTabs.map((tab) => (
              <TabsTrigger key={tab.key} value={tab.key}>
                {tab.label}（{pools[tab.key]?.length ?? 0}）
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      )}

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(row) => `${row.code}-${row.pool_type}`}
        loading={poolsQuery.isLoading}
        error={poolsQuery.error}
        onRetry={() => void poolsQuery.refetch()}
        emptyTitle="该日无池型数据"
        emptyDescription="交易时段每 10 分钟采集入库后展示。"
        renderDetail={(row) => <PoolDetail row={row} />}
      />
    </div>
  );
}
