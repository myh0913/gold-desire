/**
 * 输入区：Enter 发送（Shift+Enter 换行），流式期间禁用输入并切换为「停止」。
 */

import { useState } from 'react';
import { Send, Square } from 'lucide-react';
import { Button } from '@/components/ui/button';

/** 后端 `AgentChatRequest.message` 上限。 */
export const COMPOSER_MAX_LENGTH = 8000;

export interface AgentComposerProps {
  onSend: (text: string) => void;
  onStop: () => void;
  isStreaming: boolean;
  disabled?: boolean;
  maxLength?: number;
  /** 当前技能（用于占位提示） */
  skillName?: string | null;
}

export function AgentComposer({
  onSend,
  onStop,
  isStreaming,
  disabled = false,
  maxLength = COMPOSER_MAX_LENGTH,
  skillName,
}: AgentComposerProps) {
  const [draft, setDraft] = useState('');
  const blocked = isStreaming || disabled;
  const canSend = !blocked && draft.trim().length > 0;

  const submit = () => {
    if (!canSend) return;
    onSend(draft.trim());
    setDraft('');
  };

  return (
    <form
      className="flex shrink-0 items-end gap-2 border-t pt-3"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <textarea
        value={draft}
        onChange={(event) => setDraft(event.target.value.slice(0, maxLength))}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            submit();
          }
        }}
        rows={2}
        maxLength={maxLength}
        disabled={blocked}
        aria-label="Agent 输入框"
        placeholder={
          isStreaming
            ? 'Agent 正在回复…（可点击停止）'
            : skillName
              ? `使用「${skillName}」技能提问…`
              : '向 Agent 提问（Enter 发送，Shift+Enter 换行）'
        }
        className="border-input bg-background placeholder:text-muted-foreground focus-visible:ring-ring min-h-9 flex-1 resize-none rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 disabled:opacity-60"
      />

      <div className="flex shrink-0 flex-col items-end gap-1">
        {isStreaming ? (
          <Button type="button" variant="outline" size="icon" aria-label="停止生成" onClick={onStop}>
            <Square className="size-4" aria-hidden="true" />
          </Button>
        ) : (
          <Button type="submit" size="icon" disabled={!canSend} aria-label="发送">
            <Send className="size-4" aria-hidden="true" />
          </Button>
        )}
        <span className="text-muted-foreground text-[10px] tabular-nums">
          {draft.length}/{maxLength}
        </span>
      </div>
    </form>
  );
}
