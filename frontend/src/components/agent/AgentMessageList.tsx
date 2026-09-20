/**
 * 消息列表：自动滚动到底部，但**不打断**用户上滑查看历史。
 *
 * 判据：滚动位置距底部 < 阈值即视为「贴底」，仅在贴底时跟随新消息。
 */

import { useEffect, useRef } from 'react';
import { EmptyState, LoadingState } from '@/components/common/StateViews';
import { AgentMessageItem } from './AgentMessageItem';
import type { AgentMessage } from '@/types/agent';

/** 距底部该像素内视为贴底。 */
const STICK_THRESHOLD = 48;

export interface AgentMessageListProps {
  messages: AgentMessage[];
  loading?: boolean;
  emptyHint?: string;
}

export function AgentMessageList({ messages, loading = false, emptyHint }: AgentMessageListProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);

  const handleScroll = () => {
    const el = containerRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD;
  };

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !stickRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [messages]);

  if (loading && messages.length === 0) {
    return <LoadingState title="正在加载会话历史…" className="min-h-0 flex-1" />;
  }

  if (messages.length === 0) {
    return (
      <EmptyState
        title="尚未开始会话"
        description={emptyHint ?? '向 Agent 提问，或选择一个技能开始。'}
        className="min-h-0 flex-1"
      />
    );
  }

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      data-testid="agent-message-list"
      className="min-h-0 flex-1 overflow-y-auto pr-1"
    >
      <ul className="space-y-3">
        {messages.map((message) => (
          <AgentMessageItem key={message.id} message={message} />
        ))}
      </ul>
    </div>
  );
}
