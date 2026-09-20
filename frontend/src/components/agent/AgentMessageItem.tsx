/**
 * 单条消息：user / assistant / tool 三种角色；assistant 流式期间渲染光标。
 *
 * assistant 消息下方挂载本轮触发的工具调用卡片（由流事件累积）。
 */

import { cn } from '@/lib/utils';
import { AgentToolCallCard } from './AgentToolCallCard';
import type { AgentMessage } from '@/types/agent';

export interface AgentMessageItemProps {
  message: AgentMessage;
}

/** 工具消息（历史中的 tool 角色，内容为 JSON 载荷）。 */
function ToolMessage({ message }: AgentMessageItemProps) {
  return (
    <li data-role="tool" className="flex justify-start">
      <div className="bg-muted/20 text-muted-foreground w-full max-w-[95%] rounded-md border border-dashed px-3 py-2 text-xs">
        <div className="mb-1 font-medium">工具结果</div>
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all font-mono">
          {message.content}
        </pre>
      </div>
    </li>
  );
}

export function AgentMessageItem({ message }: AgentMessageItemProps) {
  if (message.role === 'tool') return <ToolMessage message={message} />;

  const isUser = message.role === 'user';

  return (
    <li data-role={message.role} className={cn('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'max-w-[88%] space-y-2 rounded-lg border px-3 py-2 text-sm',
          isUser ? 'bg-primary/10' : 'bg-muted/40',
        )}
      >
        <div className="break-words whitespace-pre-wrap">
          {message.content}
          {message.streaming && (
            <span
              data-testid="stream-caret"
              aria-hidden="true"
              className="bg-foreground ml-0.5 inline-block h-3.5 w-1.5 animate-pulse align-text-bottom"
            />
          )}
        </div>

        {message.toolCalls && message.toolCalls.length > 0 && (
          <div className="space-y-1">
            {message.toolCalls.map((call) => (
              <AgentToolCallCard key={call.id} call={call} />
            ))}
          </div>
        )}
      </div>
    </li>
  );
}
