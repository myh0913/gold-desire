/**
 * 空态 / 加载态 / 错误态三件套。
 *
 * 合并为一个文件（三者都是极小的纯展示组件），避免原项目每个页面各写一套。
 */

import type { ReactNode } from 'react';
import { AlertTriangle, Inbox, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';

export interface StateViewProps {
  title?: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

/** 加载中。 */
export function LoadingState({ title = '加载中…', className }: StateViewProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        'text-muted-foreground flex min-h-24 flex-col items-center justify-center gap-2 text-sm',
        className,
      )}
    >
      <Loader2 className="size-5 animate-spin" aria-hidden="true" />
      <span>{title}</span>
    </div>
  );
}

/** 空数据。 */
export function EmptyState({ title = '暂无数据', description, action, className }: StateViewProps) {
  return (
    <div
      className={cn(
        'text-muted-foreground flex min-h-24 flex-col items-center justify-center gap-2 p-6 text-center text-sm',
        className,
      )}
    >
      <Inbox className="size-5" aria-hidden="true" />
      <div className="text-foreground font-medium">{title}</div>
      {description && <div className="max-w-md text-xs">{description}</div>}
      {action}
    </div>
  );
}

/** 错误信息提取：优先后端 message。 */
export function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === 'string' && error) return error;
  return '请求失败，请稍后重试';
}

/** 错误态。 */
export function ErrorState({
  title = '加载失败',
  description,
  action,
  className,
  error,
  onRetry,
}: StateViewProps & { error?: unknown; onRetry?: () => void }) {
  const detail = description ?? (error === undefined ? undefined : errorMessage(error));
  return (
    <div
      role="alert"
      className={cn(
        'text-destructive flex min-h-24 flex-col items-center justify-center gap-2 p-6 text-center text-sm',
        className,
      )}
    >
      <AlertTriangle className="size-5" aria-hidden="true" />
      <div className="font-medium">{title}</div>
      {detail && <div className="text-muted-foreground max-w-md text-xs">{detail}</div>}
      {action}
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          重试
        </Button>
      )}
    </div>
  );
}
