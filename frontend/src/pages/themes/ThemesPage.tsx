/**
 * 主题机会页：某交易日主题强度榜 + 主题成分股（点击榜单行加载）。
 */

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { DataTable, type DataTableColumn } from '@/components/common/DataTable';
import { marketApi } from '@/lib/api';
import { formatPercentPlain, priceToneClass } from '@/lib/format';
import type { ThemeOut, ThemeStockOut } from '@/types/market';

const themeColumns = (onSelect: (theme: ThemeOut) => void): DataTableColumn<ThemeOut>[] => [
  { key: 'rank', header: '#', render: (row) => <span className="tabular-nums">{row.rank}</span> },
  { key: 'name', header: '主题', render: (row) => <span className="font-medium">{row.name}</span> },
  {
    key: 'core_avg_pct',
    header: '核心平均涨幅',
    align: 'right',
    render: (row) =>
      row.core_avg_pct != null ? (
        <span className={`tabular-nums ${priceToneClass(row.core_avg_pct)}`}>
          {formatPercentPlain(row.core_avg_pct * 100)}
        </span>
      ) : (
        '--'
      ),
  },
  {
    key: 'core_count',
    header: '核心股数',
    align: 'right',
    render: (row) => (row.core_count != null ? String(row.core_count) : '--'),
  },
  {
    key: 'action',
    header: '',
    align: 'right',
    render: (row) => (
      <button
        type="button"
        className="text-primary text-xs underline-offset-2 hover:underline"
        onClick={() => onSelect(row)}
      >
        查看成分
      </button>
    ),
  },
];

const stockColumns: DataTableColumn<ThemeStockOut>[] = [
  { key: 'code', header: '代码', render: (row) => <span className="tabular-nums">{row.code}</span> },
  { key: 'name', header: '名称', render: (row) => row.name },
  {
    key: 'price',
    header: '价格',
    align: 'right',
    render: (row) => <span className="tabular-nums">{row.price.toFixed(2)}</span>,
  },
  {
    key: 'pct',
    header: '涨幅',
    align: 'right',
    render: (row) => (
      <span className={`tabular-nums ${priceToneClass(row.pct)}`}>
        {formatPercentPlain(row.pct * 100)}
      </span>
    ),
  },
  {
    key: 'turnover',
    header: '换手率',
    align: 'right',
    render: (row) => formatPercentPlain(row.turnover_rate * 100),
  },
  {
    key: 'continue',
    header: '连板',
    align: 'right',
    render: (row) =>
      row.continue_days != null ? <Badge variant="secondary">{row.continue_days} 板</Badge> : '--',
  },
];

export default function ThemesPage() {
  const [date, setDate] = useState('');
  const [selected, setSelected] = useState<ThemeOut | null>(null);

  const datesQuery = useQuery({
    queryKey: ['market', 'themes', 'dates'],
    queryFn: ({ signal }) => marketApi.themeDates(signal),
  });
  const themesQuery = useQuery({
    queryKey: ['market', 'themes', date],
    queryFn: ({ signal }) => marketApi.themes(date ? { date } : {}, signal),
  });
  const stocksQuery = useQuery({
    queryKey: ['market', 'themes', 'stocks', selected?.trade_date, selected?.name],
    queryFn: ({ signal }) =>
      marketApi.themeStocks(selected!.trade_date, selected!.name, signal),
    enabled: selected != null,
  });

  const themes = useMemo(() => themesQuery.data?.items ?? [], [themesQuery.data]);
  const activeTheme = selected && themes.some((t) => t.name === selected.name) ? selected : null;

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">主题机会</h1>
        <div className="flex items-center gap-2">
          <Label htmlFor="themes-date" className="text-sm">
            交易日
          </Label>
          <Select
            id="themes-date"
            value={date}
            onChange={(event) => {
              setDate(event.target.value);
              setSelected(null);
            }}
            className="w-40"
          >
            <option value="">最新（{themesQuery.data?.trade_date ?? '…'}）</option>
            {(datesQuery.data?.dates ?? []).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </Select>
        </div>
      </div>

      <DataTable
        columns={themeColumns(setSelected)}
        rows={themes}
        rowKey={(row) => row.name}
        loading={themesQuery.isLoading}
        error={themesQuery.error}
        onRetry={() => void themesQuery.refetch()}
        emptyTitle="该日无主题数据"
        emptyDescription="尾盘窗口（14:45-15:00）采集题材榜后展示。"
      />

      {activeTheme && (
        <Card data-testid="theme-stocks">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">
              {activeTheme.name} · 成分股
              {activeTheme.description && (
                <span className="text-muted-foreground ml-2 text-xs font-normal">
                  {activeTheme.description}
                </span>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <DataTable
              columns={stockColumns}
              rows={stocksQuery.data?.items ?? []}
              rowKey={(row) => row.code}
              loading={stocksQuery.isLoading}
              emptyTitle="该主题无成分股"
            />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
