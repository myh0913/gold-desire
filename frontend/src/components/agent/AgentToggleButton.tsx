/**
 * Agent 抽屉开关按钮（浮动于右下角，不占用主应用布局，不遮挡内容）。
 *
 * 抽屉打开时自身隐藏，由抽屉头部关闭按钮负责收起。
 */

import { Bot } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export interface AgentToggleButtonProps {
  open: boolean;
  onClick: () => void;
  className?: string;
}

export function AgentToggleButton({ open, onClick, className }: AgentToggleButtonProps) {
  if (open) return null;

  return (
    <Button
      type="button"
      size="icon"
      aria-label="打开 Agent 助手"
      aria-expanded={false}
      data-testid="agent-toggle"
      onClick={onClick}
      className={cn('fixed right-6 bottom-6 z-40 rounded-full shadow-lg', className)}
    >
      <Bot className="size-4" aria-hidden="true" />
    </Button>
  );
}
