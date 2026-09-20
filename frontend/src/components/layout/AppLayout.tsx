/**
 * 应用外壳：侧栏 + 顶栏 + 内容区 + Agent 抽屉。
 *
 * 导航项由 `usePageAccess().pages`（后端下发的有效页面 key）派生，
 * 前端不维护页面清单。
 */

import { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
import { usePageAccess } from '@/hooks/useAuth';
import { AgentDrawer } from '@/components/agent/AgentDrawer';
import { AgentToggleButton } from '@/components/agent/AgentToggleButton';
import { Header } from './Header';
import { Sidebar } from './Sidebar';

export default function AppLayout() {
  const { pages } = usePageAccess();
  const [collapsed, setCollapsed] = useState(false);
  const [agentOpen, setAgentOpen] = useState(false);

  // Cmd/Ctrl+J 全局开关 Agent 抽屉（输入框内除外，避免抢占正常编辑快捷键）
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'j') {
        const target = event.target as HTMLElement | null;
        const inEditable =
          target?.tagName === 'INPUT' ||
          target?.tagName === 'TEXTAREA' ||
          target?.isContentEditable === true;
        if (inEditable) return;
        event.preventDefault();
        setAgentOpen((value) => !value);
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, []);

  return (
    <div className="bg-background flex h-screen overflow-hidden">
      <Sidebar pages={pages} collapsed={collapsed} />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header
          collapsed={collapsed}
          onToggleCollapsed={() => setCollapsed((value) => !value)}
          onOpenAgent={() => setAgentOpen(true)}
        />
        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
      <AgentToggleButton open={agentOpen} onClick={() => setAgentOpen(true)} />
      <AgentDrawer open={agentOpen} onOpenChange={setAgentOpen} />
    </div>
  );
}
