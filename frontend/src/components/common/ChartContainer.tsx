/**
 * recharts 容器：统一尺寸、标题与加载/空/错误态处理。
 *
 * 业务图表只需传入一个 recharts 元素作为 children，尺寸与状态由本组件兜底。
 */

import type { ReactElement, ReactNode } from 'react';
import { ResponsiveContainer } from 'recharts';
import { cn } from '@/lib/utils';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from './StateViews';

export interface ChartContainerProps {
  /** 图表高度（px，默认 280） */
  height?: number;
  loading?: boolean;
  error?: unknown;
  empty?: boolean;
  emptyTitle?: string;
  onRetry?: () => void;
  title?: ReactNode;
  description?: ReactNode;
  className?: string;
  children: ReactElement;
}

export function ChartContainer({
  height = 280,
  loading = false,
  error,
  empty = false,
  emptyTitle,
  onRetry,
  title,
  description,
  className,
  children,
}: ChartContainerProps) {
  const hasHeader = Boolean(title || description);

  return (
    <Card className={cn('overflow-hidden', className)}>
      {hasHeader && (
        <CardHeader className="pb-2">
          {title && <CardTitle className="text-sm font-medium">{title}</CardTitle>}
          {description && <CardDescription>{description}</CardDescription>}
        </CardHeader>
      )}
      <CardContent className={cn(hasHeader ? 'pt-0' : 'p-4')} style={{ minHeight: height }}>
        {loading ? (
          <LoadingState className="min-h-full" />
        ) : error !== undefined && error !== null ? (
          <ErrorState error={error} onRetry={onRetry} className="min-h-full" />
        ) : empty ? (
          <EmptyState title={emptyTitle ?? '暂无图表数据'} className="min-h-full" />
        ) : (
          <div style={{ height }}>
            <ResponsiveContainer width="100%" height="100%">
              {children}
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
