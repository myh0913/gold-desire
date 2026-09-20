/**
 * 可用工具清单（按角色由服务端过滤后的结果渲染）。
 *
 * **不硬编码权限**：是否出现「变更」标记完全取决于接口返回的 `mutating` 字段——
 * 非 admin 的清单里根本没有变更工具，因此也就没有任何变更操作入口。
 */

import { Wrench } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { AgentToolInfo } from '@/types/agent';

export interface AgentToolListProps {
  tools: AgentToolInfo[];
}

export function AgentToolList({ tools }: AgentToolListProps) {
  if (tools.length === 0) return null;

  const mutating = tools.filter((tool) => tool.mutating);

  return (
    <details data-testid="agent-tool-list" className="shrink-0 text-xs">
      <summary className="text-muted-foreground flex cursor-pointer items-center gap-1">
        <Wrench className="size-3" aria-hidden="true" />
        可用工具（{tools.length}）
        {mutating.length > 0 && (
          <span className="text-destructive">· 含 {mutating.length} 个变更工具</span>
        )}
      </summary>
      <ul className="mt-1 space-y-0.5">
        {tools.map((tool) => (
          <li key={tool.name} className="flex items-start gap-1.5">
            <span className="font-mono">{tool.name}</span>
            {tool.mutating && (
              <span
                data-testid="agent-mutating-tool"
                data-mutating="true"
                className="border-destructive/60 bg-destructive/10 text-destructive shrink-0 rounded border px-1"
              >
                变更
              </span>
            )}
            <span className={cn('text-muted-foreground truncate')}>{tool.description}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}
