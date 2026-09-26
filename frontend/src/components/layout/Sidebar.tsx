/**
 * 侧边导航：导航项由后端页面 key（`me.pages`）派生，按「行情感知 / 决策 / 系统」分组，可折叠。
 * 未登记 key 落入「其他」兜底组（见 navMeta.buildNavGroups）。
 */

import { useMemo } from 'react';
import { NavLink } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { buildNavGroups } from './navMeta';

export interface SidebarProps {
  /** 当前用户有效页面 key（后端注册表顺序） */
  pages: readonly string[];
  collapsed: boolean;
}

export function Sidebar({ pages, collapsed }: SidebarProps) {
  const groups = useMemo(() => buildNavGroups(pages), [pages]);

  return (
    <aside
      className={cn(
        'bg-card flex shrink-0 flex-col border-r transition-[width] duration-200',
        collapsed ? 'w-14' : 'w-56',
      )}
    >
      <div className="flex h-14 shrink-0 items-center border-b px-3">
        {collapsed ? (
          <span className="mx-auto text-sm font-bold tracking-tight">GD</span>
        ) : (
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold tracking-tight">gold-desire</div>
            <div className="text-muted-foreground truncate text-[10px]">A 股量化平台</div>
          </div>
        )}
      </div>

      <nav className="flex-1 overflow-y-auto p-2" aria-label="主导航">
        {groups.map((group, groupIndex) => (
          <div key={group.key} className={cn(groupIndex > 0 && 'mt-3')}>
            {!collapsed ? (
              <p className="text-muted-foreground/70 mb-1 px-3 text-[10px] font-medium tracking-widest">
                {group.label}
              </p>
            ) : (
              groupIndex > 0 && <hr className="border-border/60 mx-2 my-2" />
            )}
            {group.entries.map((entry) => {
              const Icon = entry.icon;
              const base = cn(
                'flex items-center rounded-md border border-transparent text-sm transition-colors',
                collapsed ? 'justify-center py-2' : 'gap-2.5 px-3 py-2',
              );

              if (!entry.available) {
                return (
                  <span
                    key={entry.key}
                    aria-disabled="true"
                    title={`${entry.label}（待上线）`}
                    className={cn(base, 'text-muted-foreground/50 cursor-not-allowed')}
                  >
                    <Icon className="size-4 shrink-0" aria-hidden="true" />
                    {!collapsed && <span className="truncate">{entry.label}</span>}
                  </span>
                );
              }

              return (
                <NavLink
                  key={entry.key}
                  to={entry.path}
                  end={entry.end}
                  title={entry.label}
                  className={({ isActive }) =>
                    cn(
                      base,
                      isActive
                        ? 'bg-primary/15 text-primary border-primary/30'
                        : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                    )
                  }
                >
                  <Icon className="size-4 shrink-0" aria-hidden="true" />
                  {!collapsed && <span className="truncate">{entry.label}</span>}
                </NavLink>
              );
            })}
          </div>
        ))}
      </nav>
    </aside>
  );
}
