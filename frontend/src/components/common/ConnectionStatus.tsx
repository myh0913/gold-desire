/**
 * WebSocket 连接状态指示灯。
 *
 * 挂载时自动 `connect()`；后续状态通过 `useSyncExternalStore` 订阅。
 * 显式断开请走 `ConnectionPanel` 的"断开"按钮。
 */

import { useEffect, useSyncExternalStore } from 'react';
import { cn } from '@/lib/utils';
import { getWsClient } from '@/lib/ws';
import type { WsStatus } from '@/types';

const STATUS_META: Record<WsStatus, { label: string; dot: string; text: string }> = {
  open: { label: '实时已连接', dot: 'bg-stock-down', text: 'text-stock-down' },
  connecting: { label: '连接中…', dot: 'bg-amber-400 animate-pulse', text: 'text-amber-400' },
  closed: { label: '实时未连接', dot: 'bg-muted-foreground', text: 'text-muted-foreground' },
};

export interface ConnectionStatusProps {
  className?: string;
  /** 是否显示文字（收起侧栏时只显示圆点） */
  showLabel?: boolean;
}

export function ConnectionStatus({ className, showLabel = true }: ConnectionStatusProps) {
  const status = useSyncExternalStore(
    (onChange) => getWsClient().onStatus(() => onChange()),
    () => getWsClient().status,
  );

  // 挂载时自动发起连接；useEffect 只跑一次。
  useEffect(() => {
    getWsClient().connect();
  }, []);

  const meta = STATUS_META[status];

  return (
    <span
      role="status"
      title={meta.label}
      aria-label={meta.label}
      className={cn('inline-flex items-center gap-1.5 text-xs', meta.text, className)}
    >
      <span className={cn('size-2 shrink-0 rounded-full', meta.dot)} aria-hidden="true" />
      {showLabel && <span>{meta.label}</span>}
    </span>
  );
}
