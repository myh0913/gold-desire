/**
 * 7×24 快讯页：关键词检索 + 分页；WS `newsflash`/采集事件触发自动失效。
 *
 * 不展示「级别」也不按级别筛选：数据源 newsflash 接口的 `impact` 实测恒为 0
 * （259 条样本无一例外），级别字段没有可用取值。
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { DataTable, type DataTableColumn } from '@/components/common/DataTable';
import { StaleNotice } from '@/components/common/StaleNotice';
import { useChannelRefresh } from '@/hooks/useChannelRefresh';
import { marketApi } from '@/lib/api';
import { fmtDateTime } from '@/lib/time';
import type { NewsFlashOut } from '@/types/market';

const PAGE_SIZE = 20;

const columns: DataTableColumn<NewsFlashOut>[] = [
  {
    key: 'ts',
    header: '时间',
    render: (row) => (
      <span className="text-muted-foreground tabular-nums text-xs">{fmtDateTime(row.ts)}</span>
    ),
  },
  {
    key: 'title',
    header: '标题',
    render: (row) => <span className="font-medium">{row.title}</span>,
  },
  {
    key: 'summary',
    header: '摘要',
    render: (row) => <span className="text-muted-foreground text-xs">{row.summary ?? '--'}</span>,
  },
  {
    key: 'symbols',
    header: '关联标的',
    render: (row) =>
      row.symbols.length > 0 ? (
        <span className="tabular-nums text-xs">{row.symbols.join(' / ')}</span>
      ) : (
        '--'
      ),
  },
];

export default function NewsflashPage() {
  useChannelRefresh(['newsflash'], ['market', 'newsflash']);
  const [keyword, setKeyword] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);

  const newsQuery = useQuery({
    queryKey: ['market', 'newsflash', query, page],
    queryFn: ({ signal }) =>
      marketApi.newsflash({ keyword: query || undefined, page, page_size: PAGE_SIZE }, signal),
  });

  return (
    <div className="space-y-4 p-4">
      <h1 className="text-xl font-semibold">7×24 快讯</h1>

      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="news-keyword" className="text-xs">
            关键词
          </Label>
          <Input
            id="news-keyword"
            value={keyword}
            placeholder="标题/摘要检索，回车生效"
            onChange={(event) => setKeyword(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                setQuery(keyword.trim());
                setPage(1);
              }
            }}
            className="w-64"
          />
        </div>
      </div>

      <StaleNotice
        stale={newsQuery.data?.stale}
        dataDate={newsQuery.data?.data_date}
        label="快讯"
      />

      <DataTable
        columns={columns}
        rows={newsQuery.data?.items}
        rowKey={(row) => row.ts}
        loading={newsQuery.isLoading}
        error={newsQuery.error}
        onRetry={() => void newsQuery.refetch()}
        emptyTitle="暂无快讯"
        emptyDescription="盘中窗口（09:26-10:00）按间隔采集。"
        page={page}
        pageSize={PAGE_SIZE}
        total={newsQuery.data?.total}
        onPageChange={setPage}
      />
    </div>
  );
}
