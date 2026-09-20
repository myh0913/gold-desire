/**
 * 单次工具调用卡片：可折叠，展示工具名 / 入参 / 状态 / 耗时。
 *
 * 被拒（denied）的调用以 destructive 配色**显著区分**，便于识别越权。
 */

import { useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { AgentToolCall, AgentToolCallStatus } from '@/types/agent';

const STATUS_META: Record<AgentToolCallStatus, { label: string; tone: string }> = {
  running: { label: '执行中', tone: 'border-sky-500/40 bg-sky-500/10 text-sky-600' },
  ok: { label: '成功', tone: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600' },
  denied: { label: '已拒绝', tone: 'border-destructive/60 bg-destructive/15 text-destructive' },
  error: { label: '失败', tone: 'border-amber-500/40 bg-amber-500/10 text-amber-600' },
  pending: { label: '待确认', tone: 'border-amber-500/40 bg-amber-500/10 text-amber-600' },
};

export interface AgentToolCallCardProps {
  call: AgentToolCall;
}

export function AgentToolCallCard({ call }: AgentToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);
  const meta = STATUS_META[call.status];
  const denied = call.status === 'denied';

  return (
    <div
      data-testid="tool-call-card"
      data-status={call.status}
      className={cn(
        'rounded-md border text-xs',
        denied ? 'border-destructive/60 bg-destructive/5' : 'bg-muted/30',
      )}
    >
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left"
      >
        <ChevronRight
          className={cn('size-3 shrink-0 transition-transform', expanded && 'rotate-90')}
          aria-hidden="true"
        />
        <span className={cn('truncate font-mono', denied && 'text-destructive')}>{call.tool}</span>
        <span className={cn('ml-auto shrink-0 rounded border px-1.5 py-0.5', meta.tone)}>
          {meta.label}
        </span>
        {call.durationMs !== undefined && (
          <span className="text-muted-foreground shrink-0 tabular-nums">{call.durationMs}ms</span>
        )}
      </button>

      {expanded && (
        <div className="space-y-1 border-t px-2 py-1.5">
          <div className="text-muted-foreground">参数</div>
          <pre className="bg-background max-h-40 overflow-auto rounded border p-1.5 font-mono whitespace-pre-wrap">
            {JSON.stringify(call.arguments, null, 2)}
          </pre>
          {call.summary && (
            <>
              <div className="text-muted-foreground">结果摘要</div>
              <pre className="bg-background max-h-40 overflow-auto rounded border p-1.5 whitespace-pre-wrap">
                {call.summary}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}
