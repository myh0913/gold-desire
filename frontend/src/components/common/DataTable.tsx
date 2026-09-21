/**
 * 泛型数据表格：列配置 + 加载/空/错误态 + 可选分页。
 *
 * 泛型用函数声明（而非箭头函数）书写，避免 .tsx 下的泛型解析歧义。
 */

import { Fragment, useState, type ReactNode } from 'react';
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
  /**
   * 可选：为每行渲染**可展开详情**。提供后表格末尾自动追加「详情」列，
   * 展开时插入一整行（``colSpan`` 覆盖全部列）承载详情内容。
   *
   * 缺省不传时表格行为与结构完全不变（向后兼容）。展开状态按 ``rowKey`` 记忆，
   * 同一时刻只展开一行。
   */
  renderDetail?: (row: T, index: number) => ReactNode;
  /** 详情列的表头文案（缺省「详情」） */
  detailHeader?: ReactNode;
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
  renderDetail,
  detailHeader = '详情',
  className,
}: DataTableProps<T>) {
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

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
  const columnCount = columns.length + (renderDetail ? 1 : 0);
  const expandable = renderDetail !== undefined;

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
              {expandable && (
                <th scope="col" className="w-20 px-3 py-2 text-center font-medium">
                  {detailHeader}
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {list.map((row, index) => {
              const key = rowKey(row, index);
              const isOpen = expandable && expandedKey === key;
              return (
                <Fragment key={key}>
                  <tr className="hover:bg-muted/30 border-t transition-colors">
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
                    {expandable && (
                      <td className="px-3 py-2 text-center">
                        <Button
                          variant="ghost"
                          size="sm"
                          aria-expanded={isOpen}
                          onClick={() => setExpandedKey(isOpen ? null : key)}
                        >
                          {isOpen ? '收起' : '展开'}
                        </Button>
                      </td>
                    )}
                  </tr>
                  {isOpen && renderDetail && (
                    <tr className="bg-muted/20 border-t">
                      <td colSpan={columnCount} className="px-3 py-3">
                        {renderDetail(row, index)}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
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
