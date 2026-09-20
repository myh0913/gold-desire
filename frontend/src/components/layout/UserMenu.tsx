/**
 * 用户菜单：用户名 + 角色 + 退出登录。
 */

import { useState } from 'react';
import { ChevronDown, LogOut, UserRound } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/hooks/useAuth';
import { roleLabel } from '@/lib/roles';

export function UserMenu() {
  const { me, logout } = useAuth();
  const [open, setOpen] = useState(false);

  if (!me) return null;

  return (
    <div className="relative">
      <Button
        variant="ghost"
        size="sm"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <UserRound className="size-4" aria-hidden="true" />
        <span className="max-w-24 truncate">{me.user.username}</span>
        <ChevronDown className="size-3" aria-hidden="true" />
      </Button>

      {open && (
        <>
          <button
            type="button"
            aria-hidden="true"
            tabIndex={-1}
            className="fixed inset-0 z-40 cursor-default"
            onClick={() => setOpen(false)}
          />
          <div
            role="menu"
            className="bg-popover text-popover-foreground absolute right-0 z-50 mt-1 w-48 rounded-md border p-1 shadow-md"
          >
            <div className="px-2 py-1.5">
              <div className="truncate text-sm font-medium">{me.user.username}</div>
              <div className="text-muted-foreground text-xs">{roleLabel(me.role)}</div>
            </div>
            <div className="bg-border my-1 h-px" />
            <button
              type="button"
              role="menuitem"
              className="hover:bg-accent hover:text-accent-foreground flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm transition-colors"
              onClick={() => {
                setOpen(false);
                logout.mutate();
              }}
            >
              <LogOut className="size-4" aria-hidden="true" />
              退出登录
            </button>
          </div>
        </>
      )}
    </div>
  );
}
