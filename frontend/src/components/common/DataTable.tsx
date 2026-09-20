/**
 * 泛型数据表格：列配置 + 加载/空/错误态 + 可选分页。
 *
 * 泛型用函数声明（而非箭头函数）书写，避免 .tsx 下的泛型解析歧义。
 */

import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { EmptyState, ErrorState, LoadingState } from './StateViews';

export interface DataTableColumn<T> {
  /** 列标识（稳定唯一） */
  key: string;
  header: ReactNode;
  render: (row: T, index: number) => ReactNode;
  align?: 'left' | 'center' | 'right';
  className?: string;
}

export interface DataTableProps<T> {
  columns: DataTableColumn<T>[];
  rows: readonly T[] | undefined;
  rowKey: (row: T, index: number) => string;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
  emptyTitle?: string;
  emptyDescription?: string;
  /** 分页（四项齐备时显示分页条） */
  page?: number;
  pageSize?: number;
  total?: number;
  onPageChange?: (page: number) => void;
  className?: string;
}

const ALIGN_CLASS = {
  left: 'text-left',
  center: 'text-center',
  right: 'text-right',
} as const;

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  loading = false,
  error,
  onRetry,
  emptyTitle,
  emptyDescription,
  page,
  pageSize,
  total,
  onPageChange,
  className,
}: DataTableProps<T>) {
  if (loading) return <LoadingState className={className} />;
  if (error !== undefined && error !== null) {
    return <ErrorState error={error} onRetry={onRetry} className={className} />;
  }

  const list = rows ?? [];
  if (list.length === 0) {
    return (
      <EmptyState title={emptyTitle} description={emptyDescription} className={className} />
    );
  }

  const currentPage = page ?? 1;
  const totalPages = pageSize ? Math.max(1, Math.ceil((total ?? list.length) / pageSize)) : 1;
  const pagerVisible = pageSize !== undefined && total !== undefined && onPageChange !== undefined;

  return (
    <div className={cn('space-y-3', className)}>
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full border-collapse text-sm">
          <thead className="bg-muted/40 text-muted-foreground">
            <tr>
              {columns.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={cn(
                    'px-3 py-2 font-medium',
                    ALIGN_CLASS[column.align ?? 'left'],
                    column.className,
                  )}
                >
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {list.map((row, index) => (
              <tr key={rowKey(row, index)} className="hover:bg-muted/30 border-t transition-colors">
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={cn(
                      'px-3 py-2',
                      ALIGN_CLASS[column.align ?? 'left'],
                      column.className,
                    )}
                  >
                    {column.render(row, index)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {pagerVisible && (
        <div className="text-muted-foreground flex items-center justify-between text-xs">
          <span>
            共 {total} 条 · 第 {currentPage} / {totalPages} 页
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={currentPage <= 1}
              onClick={() => onPageChange?.(currentPage - 1)}
            >
              上一页
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={currentPage >= totalPages}
              onClick={() => onPageChange?.(currentPage + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
