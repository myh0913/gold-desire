/**
 * Agent 右侧抽屉外壳：会话切换 + 流式消息 + 技能选择 + 输入区 + HITL 确认。
 *
 * **不阻塞主应用**：外壳包一层 `pointer-events-none`，仅把抽屉面板自身恢复为
 * `pointer-events-auto`——遮罩不再拦截主应用的点击与滚动；全程无全局 loading。
 */

import { useState } from 'react';
import { Sheet } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Select } from '@/components/ui/select';
import { useAgent } from '@/hooks/useAgent';
import { AgentComposer } from './AgentComposer';
import { AgentConfirmDialog } from './AgentConfirmDialog';
import { AgentErrorBanner } from './AgentErrorBanner';
import { AgentMessageList } from './AgentMessageList';
import { AgentSkillPicker } from './AgentSkillPicker';
import { AgentToolList } from './AgentToolList';

export interface AgentDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AgentDrawer({ open, onOpenChange }: AgentDrawerProps) {
  const agent = useAgent({ active: open });
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null);

  return (
    <>
      <div
        data-testid="agent-drawer-root"
        className="pointer-events-none [&_[role=dialog]]:pointer-events-auto"
      >
        <Sheet
          open={open}
          onOpenChange={onOpenChange}
          side="right"
          title="Agent 助手"
          description="运维与策略调参辅助（流式，可中断）"
          lockScroll={false}
          className="max-w-xl"
        >
          <div className="flex h-full flex-col gap-3">
            <div className="flex shrink-0 items-center gap-2">
              <Select
                aria-label="选择会话"
                className="h-8 flex-1 text-xs"
                value={agent.sessionId ?? ''}
                disabled={agent.isStreaming}
                onChange={(event) => {
                  const value = event.target.value;
                  if (value) agent.selectSession(value);
                  else agent.newSession();
                }}
              >
                <option value="">新会话</option>
                {agent.sessions.map((session) => (
                  <option key={session.session_id} value={session.session_id}>
                    {session.title ?? session.session_id.slice(0, 8)}
                  </option>
                ))}
              </Select>
              <Button
                variant="outline"
                size="sm"
                disabled={agent.isStreaming}
                onClick={agent.newSession}
              >
                新会话
              </Button>
            </div>

            <AgentErrorBanner
              error={agent.error}
              notice={agent.notice}
              onDismissError={agent.dismissError}
              onDismissNotice={agent.dismissNotice}
              onRetry={agent.retry}
            />

            <AgentMessageList messages={agent.messages} loading={agent.isLoadingHistory} />
            <AgentToolList tools={agent.tools} />
            <AgentSkillPicker
              skills={agent.skills}
              selected={selectedSkill}
              onSelect={setSelectedSkill}
            />
            <AgentComposer
              onSend={(text) => agent.sendMessage(text, selectedSkill)}
              onStop={agent.stop}
              isStreaming={agent.isStreaming}
              skillName={selectedSkill}
            />
          </div>
        </Sheet>
      </div>

      <AgentConfirmDialog
        action={agent.pendingAction}
        isConfirming={agent.isConfirming}
        onConfirm={agent.confirm}
        onCancel={agent.cancelConfirmation}
      />
    </>
  );
}
