/**
 * 告警横幅：支持 `alert`（上游/任务故障）与 `stale`（后端标记的陈旧数据）两种变体。
 *
 * 可关闭 + 自动隐藏（默认 8s；传 `autoHideMs={0}` 关闭自动隐藏）。
 */

import { useEffect, useState } from 'react';
import { AlertTriangle, Clock, X } from 'lucide-react';
import { cn } from '@/lib/utils';

export type AlertVariant = 'alert' | 'stale';

export interface AlertBannerProps {
  variant?: AlertVariant;
  title?: string;
  message: string;
  onDismiss?: () => void;
  /** 自动隐藏毫秒数；0 表示不自动隐藏 */
  autoHideMs?: number;
  className?: string;
}

const VARIANT_CLASS: Record<AlertVariant, string> = {
  alert: 'border-destructive/40 bg-destructive/10 text-destructive',
  stale: 'border-amber-500/40 bg-amber-500/10 text-amber-400',
};

const DEFAULT_TITLE: Record<AlertVariant, string> = {
  alert: '异常告警',
  stale: '数据可能不是最新',
};

export function AlertBanner({
  variant = 'alert',
  title,
  message,
  onDismiss,
  autoHideMs = 8000,
  className,
}: AlertBannerProps) {
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    setVisible(true);
  }, [message, variant]);

  useEffect(() => {
    if (!visible || autoHideMs <= 0 || !onDismiss) return;
    const timer = window.setTimeout(() => {
      setVisible(false);
      onDismiss();
    }, autoHideMs);
    return () => window.clearTimeout(timer);
  }, [visible, autoHideMs, onDismiss]);

  if (!visible || !message) return null;

  const Icon = variant === 'stale' ? Clock : AlertTriangle;

  return (
    <div
      role={variant === 'alert' ? 'alert' : 'status'}
      className={cn(
        'flex items-start gap-2 rounded-md border px-3 py-2 text-sm',
        VARIANT_CLASS[variant],
        className,
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <div className="font-medium">{title ?? DEFAULT_TITLE[variant]}</div>
        <div className="break-words opacity-90">{message}</div>
      </div>
      {onDismiss && (
        <button
          type="button"
          aria-label="关闭提示"
          className="shrink-0 rounded p-0.5 opacity-70 transition-opacity hover:opacity-100"
          onClick={() => {
            setVisible(false);
            onDismiss();
          }}
        >
          <X className="size-4" />
        </button>
      )}
    </div>
  );
}
