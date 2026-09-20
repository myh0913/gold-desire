/**
 * 抽屉内联横幅：错误（可关闭、可重试）与信息提示（如预算耗尽）两态。
 *
 * 均为**行内**展示，不使用全局遮罩，保证主应用不受影响。
 */

import { AlertTriangle, Info, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { AgentInlineError } from '@/types/agent';

export interface AgentErrorBannerProps {
  error?: AgentInlineError | null;
  notice?: string | null;
  onDismissError?: () => void;
  onDismissNotice?: () => void;
  onRetry?: () => void;
}

export function AgentErrorBanner({
  error,
  notice,
  onDismissError,
  onDismissNotice,
  onRetry,
}: AgentErrorBannerProps) {
  if (!error && !notice) return null;

  return (
    <div className="shrink-0 space-y-2">
      {error && (
        <div
          role="alert"
          data-testid="agent-error"
          className="border-destructive/40 bg-destructive/10 text-destructive flex items-start gap-2 rounded-md border px-3 py-2 text-sm"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div className="min-w-0 flex-1 break-words">{error.message}</div>
          {error.retryable && onRetry && (
            <Button variant="outline" size="sm" className="shrink-0" onClick={onRetry}>
              重试
            </Button>
          )}
          {onDismissError && (
            <button
              type="button"
              aria-label="关闭错误提示"
              onClick={onDismissError}
              className="shrink-0 rounded p-0.5 opacity-70 transition-opacity hover:opacity-100"
            >
              <X className="size-4" aria-hidden="true" />
            </button>
          )}
        </div>
      )}

      {notice && (
        <div
          role="status"
          data-testid="agent-notice"
          className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-600"
        >
          <Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div className="min-w-0 flex-1 break-words">{notice}</div>
          {onDismissNotice && (
            <button
              type="button"
              aria-label="关闭提示"
              onClick={onDismissNotice}
              className="shrink-0 rounded p-0.5 opacity-70 transition-opacity hover:opacity-100"
            >
              <X className="size-4" aria-hidden="true" />
            </button>
          )}
        </div>
      )}
    </div>
  );
}
