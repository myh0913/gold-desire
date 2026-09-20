/**
 * 顶栏：侧栏折叠开关 + 实时连接状态 + Agent 抽屉入口 + 用户菜单。
 */

import { Bot, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ConnectionStatus } from '@/components/common/ConnectionStatus';
import { UserMenu } from './UserMenu';

export interface HeaderProps {
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onOpenAgent: () => void;
}

export function Header({ collapsed, onToggleCollapsed, onOpenAgent }: HeaderProps) {
  return (
    <header className="bg-card flex h-14 shrink-0 items-center gap-3 border-b px-4">
      <Button
        variant="ghost"
        size="icon"
        aria-label={collapsed ? '展开菜单栏' : '收起菜单栏'}
        title={collapsed ? '展开菜单栏' : '收起菜单栏'}
        onClick={onToggleCollapsed}
      >
        {collapsed ? (
          <PanelLeftOpen className="size-4" aria-hidden="true" />
        ) : (
          <PanelLeftClose className="size-4" aria-hidden="true" />
        )}
      </Button>

      <ConnectionStatus className="hidden sm:inline-flex" />

      <div className="ml-auto flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={onOpenAgent}>
          <Bot className="size-4" aria-hidden="true" />
          Agent
        </Button>
        <UserMenu />
      </div>
    </header>
  );
}
