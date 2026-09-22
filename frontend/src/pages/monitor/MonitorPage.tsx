/**
 * 监管名单页：某日重点监控 / 风险警示名单，支持类别过滤。
 */

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { DataTable, type DataTableColumn } from '@/components/common/DataTable';
import { StaleNotice } from '@/components/common/StaleNotice';
import { marketApi } from '@/lib/api';
import type { MonitorStockOut } from '@/types/market';

/** 类别中文映射（口径见 ingest/tasks.py：severe=002 严重异常波动，unusual=001 普通异常波动）。 */
const KIND_LABEL: Record<string, string> = {
  restricted: '重点监控',
  severe: '严重异常波动',
  unusual: '异常波动',
};

const kindLabel = (kind: string): string => KIND_LABEL[kind] ?? kind;

const columns: DataTableColumn<MonitorStockOut>[] = [
  { key: 'code', header: '代码', render: (row) => <span className="tabular-nums">{row.code}</span> },
  { key: 'name', header: '名称', render: (row) => row.name },
  {
    key: 'kind',
    header: '类别',
    render: (row) => <Badge variant="outline">{kindLabel(row.kind)}</Badge>,
  },
  {
    key: 'reason',
    header: '原因',
    render: (row) => <span className="text-muted-foreground text-xs">{row.reason ?? '--'}</span>,
  },
];

export default function MonitorPage() {
  const [date, setDate] = useState('');
  const [kind, setKind] = useState('');

  const monitorQuery = useQuery({
    queryKey: ['market', 'monitor', date, kind],
    queryFn: ({ signal }) =>
      marketApi.monitor({ date: date || undefined, kind: kind || undefined }, signal),
  });

  const items = monitorQuery.data?.items ?? [];
  const kinds = useMemo(() => [...new Set(items.map((row) => row.kind))].sort(), [items]);

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">监管名单</h1>
        <div className="flex items-center gap-2">
          <Label htmlFor="monitor-date" className="text-sm">
            交易日
          </Label>
          <Select
            id="monitor-date"
            value={date}
            onChange={(event) => setDate(event.target.value)}
            className="w-40"
          >
            <option value="">最新（{monitorQuery.data?.trade_date ?? '…'}）</option>
          </Select>
          <Label htmlFor="monitor-kind" className="text-sm">
            类别
          </Label>
          <Select
            id="monitor-kind"
            value={kind}
            onChange={(event) => setKind(event.target.value)}
            className="w-36"
          >
            <option value="">全部</option>
            {kinds.map((item) => (
              <option key={item} value={item}>
                {kindLabel(item)}
              </option>
            ))}
          </Select>
        </div>
      </div>

      <StaleNotice
        stale={monitorQuery.data?.stale}
        dataDate={monitorQuery.data?.data_date}
        label="监管名单"
      />

      <DataTable
        columns={columns}
        rows={items}
        rowKey={(row) => `${row.kind}-${row.code}`}
        loading={monitorQuery.isLoading}
        error={monitorQuery.error}
        onRetry={() => void monitorQuery.refetch()}
        emptyTitle="该日无监管名单"
      />
    </div>
  );
}
