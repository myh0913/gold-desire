/**
 * 7×24 快讯页：级别过滤 + 关键词检索 + 分页；WS `pool`/采集事件触发自动失效（总览订阅共用）。
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { DataTable, type DataTableColumn } from '@/components/common/DataTable';
import { StaleNotice } from '@/components/common/StaleNotice';
import { useChannelRefresh } from '@/hooks/useChannelRefresh';
import { marketApi } from '@/lib/api';
import { fmtDateTime } from '@/lib/time';
import type { NewsFlashOut } from '@/types/market';

const PAGE_SIZE = 20;

const LEVEL_LABEL: Record<string, string> = { high: '重要', mid: '一般', low: '低' };

function levelVariant(level: string | null): 'up' | 'secondary' | 'outline' {
  if (level === 'high') return 'up';
  if (level === 'mid') return 'secondary';
  return 'outline';
}

const columns: DataTableColumn<NewsFlashOut>[] = [
  {
    key: 'ts',
    header: '时间',
    render: (row) => (
      <span className="text-muted-foreground tabular-nums text-xs">
        {fmtDateTime(row.ts)}
      </span>
    ),
  },
  {
    key: 'level',
    header: '级别',
    render: (row) => (
      <Badge variant={levelVariant(row.level)}>{LEVEL_LABEL[row.level ?? ''] ?? row.level ?? '--'}</Badge>
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
  const [level, setLevel] = useState('');
  const [keyword, setKeyword] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);

  const newsQuery = useQuery({
    queryKey: ['market', 'newsflash', level, query, page],
    queryFn: ({ signal }) =>
      marketApi.newsflash(
        { level: level || undefined, keyword: query || undefined, page, page_size: PAGE_SIZE },
        signal,
      ),
  });

  return (
    <div className="space-y-4 p-4">
      <h1 className="text-xl font-semibold">7×24 快讯</h1>

      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="news-level" className="text-xs">
            级别
          </Label>
          <Select
            id="news-level"
            value={level}
            onChange={(event) => {
              setLevel(event.target.value);
              setPage(1);
            }}
            className="w-28"
          >
            <option value="">全部</option>
            <option value="high">重要</option>
            <option value="mid">一般</option>
            <option value="low">低</option>
          </Select>
        </div>
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
