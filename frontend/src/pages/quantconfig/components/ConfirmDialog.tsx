/**
 * 轻量二次确认对话框（危险操作前人工介入）。
 *
 * 不使用 `window.confirm`，以便与页面样式一致并可被测试驱动。
 */

import type { ReactNode } from 'react';
import { Button } from '@/components/ui/button';

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = '确认',
  cancelLabel = '取消',
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="bg-card w-full max-w-sm space-y-4 rounded-lg border p-5 shadow-lg"
      >
        <div className="space-y-2">
          <h2 className="text-base font-semibold">{title}</h2>
          {description && <div className="text-muted-foreground text-sm">{description}</div>}
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" disabled={busy} onClick={onCancel}>
            {cancelLabel}
          </Button>
          <Button variant="destructive" size="sm" disabled={busy} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
