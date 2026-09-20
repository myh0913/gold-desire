/**
 * HITL 危险操作确认弹窗。
 *
 * 后端对 `requires_confirmation` 的工具**不会**执行，只登记一次性令牌；
 * 必须由用户在此显式点击「确认执行」才会调用 `/confirm` 端点。因此本弹窗
 * 是真实的确认步骤，绝无自动执行路径。
 */

import { ShieldAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { fmtDateTime } from '@/lib/time';
import type { AgentPendingAction } from '@/types/agent';

export interface AgentConfirmDialogProps {
  action: AgentPendingAction | null;
  isConfirming: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function AgentConfirmDialog({
  action,
  isConfirming,
  onConfirm,
  onCancel,
}: AgentConfirmDialogProps) {
  if (!action) return null;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" role="presentation">
      <div
        className="bg-background/80 absolute inset-0 backdrop-blur-sm"
        onClick={isConfirming ? undefined : onCancel}
        aria-hidden="true"
      />
      <div
        role="alertdialog"
        aria-modal="true"
        aria-label="危险操作确认"
        data-testid="agent-confirm-dialog"
        className="bg-background relative w-full max-w-md space-y-3 rounded-lg border p-5 shadow-lg"
      >
        <div className="text-destructive flex items-center gap-2">
          <ShieldAlert className="size-4" aria-hidden="true" />
          <span className="font-semibold">危险操作确认</span>
        </div>

        <p className="text-muted-foreground text-sm">
          Agent 请求执行以下变更操作，确认后才会真正执行（一次性令牌）。
        </p>

        <div className="bg-muted/30 space-y-1 rounded-md border p-2 text-xs">
          <div className="font-mono font-medium">{action.tool}</div>
          <pre className="max-h-40 overflow-auto font-mono whitespace-pre-wrap break-all">
            {JSON.stringify(action.arguments, null, 2)}
          </pre>
        </div>

        {action.expires_at && (
          <p className="text-muted-foreground text-[11px]">
            有效期至 {fmtDateTime(action.expires_at)}（Asia/Shanghai）
          </p>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onCancel} disabled={isConfirming}>
            取消
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={isConfirming}>
            {isConfirming ? '执行中…' : '确认执行'}
          </Button>
        </div>
      </div>
    </div>
  );
}
