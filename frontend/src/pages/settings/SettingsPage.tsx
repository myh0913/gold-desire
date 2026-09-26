/**
 * 设置页（Phase 1）：个人资料（本金）/ 用户 / 角色权限 / 邀请码 / 连接。
 *
 * 「个人」tab 恒可见（任何能进设置页的用户都可维护自己的本金）；
 * 管理端 tab 仅 admin 可见 —— 所有 `/admin/*` 接口服务端仍会校验（权威在服务端）。
 */

import { useMemo, useState } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { usePageAccess } from '@/hooks/useAuth';
import { ConnectionPanel } from './ConnectionPanel';
import { InvitationsPanel } from './InvitationsPanel';
import { ProfilePanel } from './ProfilePanel';
import { RolesPanel } from './RolesPanel';
import { UsersPanel } from './UsersPanel';

const PROFILE_TAB = { value: 'profile', label: '个人' } as const;

const ADMIN_TABS = [
  { value: 'users', label: '用户' },
  { value: 'roles', label: '角色权限' },
  { value: 'invitations', label: '邀请码' },
  { value: 'connection', label: '连接' },
] as const;

export default function SettingsPage() {
  const { isAdmin } = usePageAccess();
  const [tab, setTab] = useState<string>(PROFILE_TAB.value);

  const tabs = useMemo(
    () => (isAdmin ? [PROFILE_TAB, ...ADMIN_TABS] : [PROFILE_TAB]),
    [isAdmin],
  );
  const active = tabs.some((item) => item.value === tab) ? tab : PROFILE_TAB.value;

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">设置</h1>
        <p className="text-muted-foreground text-sm">
          个人资料、用户、角色页面权限、邀请码与实时连接。页面权限矩阵由后端注册表派生。
        </p>
      </header>

      <Tabs value={active} onValueChange={setTab}>
        <TabsList>
          {tabs.map((item) => (
            <TabsTrigger key={item.value} value={item.value}>
              {item.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="profile">
          <ProfilePanel />
        </TabsContent>
        {isAdmin && (
          <>
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
          </>
        )}
      </Tabs>
    </div>
  );
}
