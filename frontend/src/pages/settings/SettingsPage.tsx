/**
 * 设置页（Phase 1）：用户 / 角色权限 / 邀请码 / 连接。
 *
 * 页面可访问性由路由守卫按后端下发的 `settings` key 判定；
 * 此处再做一次管理员校验，因为所有 `/admin/*` 接口仅 admin 可用（服务端仍为权威）。
 */

import { useState } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ErrorState } from '@/components/common/StateViews';
import { usePageAccess } from '@/hooks/useAuth';
import { ConnectionPanel } from './ConnectionPanel';
import { InvitationsPanel } from './InvitationsPanel';
import { RolesPanel } from './RolesPanel';
import { UsersPanel } from './UsersPanel';

const TABS = [
  { value: 'users', label: '用户' },
  { value: 'roles', label: '角色权限' },
  { value: 'invitations', label: '邀请码' },
  { value: 'connection', label: '连接' },
] as const;

export default function SettingsPage() {
  const { isAdmin } = usePageAccess();
  const [tab, setTab] = useState<string>('users');

  if (!isAdmin) {
    return (
      <div className="p-6">
        <ErrorState title="无权限" description="设置页仅管理员可访问。" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">设置</h1>
        <p className="text-muted-foreground text-sm">
          用户、角色页面权限、邀请码与实时连接。页面权限矩阵由后端注册表派生。
        </p>
      </header>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          {TABS.map((item) => (
            <TabsTrigger key={item.value} value={item.value}>
              {item.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="users">
          <UsersPanel />
        </TabsContent>
        <TabsContent value="roles">
          <RolesPanel />
        </TabsContent>
        <TabsContent value="invitations">
          <InvitationsPanel />
        </TabsContent>
        <TabsContent value="connection">
          <ConnectionPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}
