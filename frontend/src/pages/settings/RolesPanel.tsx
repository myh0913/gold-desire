/**
 * 角色与页面权限面板。
 *
 * 矩阵列集来自后端注册表（`usePageRegistry`）；保存时把**全部**勾选 key 提交，
 * 服务端 `validate_page_keys` 会拒绝未知 key，因此不会静默丢页。
 */

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ErrorState, LoadingState, errorMessage } from '@/components/common/StateViews';
import {
  useCreateRoleMutation,
  useDeleteRoleMutation,
  useResetRolePagesMutation,
  useSetRolePagesMutation,
} from '@/lib/queries';
import type { RoleOut } from '@/types';
import { RoleMatrix } from './RoleMatrix';
import { usePageRegistry } from './hooks/usePageRegistry';
import { useRolesDraft } from './hooks/useRolesDraft';

const ROLE_NAME_PATTERN = /^[a-z][a-z0-9_]*$/;

export function RolesPanel() {
  const { roles, pages, loading, error, refetch } = usePageRegistry();
  const { draft, isDirty, toggle, discard } = useRolesDraft(roles);

  const setPages = useSetRolePagesMutation();
  const resetPages = useResetRolePagesMutation();
  const createRole = useCreateRoleMutation();
  const deleteRole = useDeleteRoleMutation();

  const [newRole, setNewRole] = useState({ name: '', label: '' });
  const [notice, setNotice] = useState('');
  const [localError, setLocalError] = useState('');

  const busy =
    setPages.isPending || resetPages.isPending || createRole.isPending || deleteRole.isPending;
  const mutationError = setPages.error ?? resetPages.error ?? createRole.error ?? deleteRole.error;

  if (loading) return <LoadingState />;
  if (error) return <ErrorState error={error} onRetry={refetch} />;

  const handleSave = (name: string) => {
    setNotice('');
    setPages.mutate(
      { name, body: { pages: draft[name] ?? [] } },
      { onSuccess: () => setNotice(`已保存角色「${name}」的页面权限`) },
    );
  };

  const handleReset = (name: string) => {
    setNotice('');
    resetPages.mutate(name, {
      onSuccess: () => setNotice(`已把角色「${name}」重置为注册表默认`),
    });
  };

  const handleDelete = (role: RoleOut) => {
    if (!window.confirm(`确认删除角色「${role.label}」？该操作不可撤销。`)) return;
    setNotice('');
    deleteRole.mutate(role.name, { onSuccess: () => setNotice(`已删除角色「${role.name}」`) });
  };

  const handleCreate = () => {
    setLocalError('');
    setNotice('');
    const name = newRole.name.trim();
    const label = newRole.label.trim();
    if (!ROLE_NAME_PATTERN.test(name)) {
      setLocalError('角色标识须为小写字母开头，仅含小写字母、数字与下划线');
      return;
    }
    if (!label) {
      setLocalError('请填写角色显示名');
      return;
    }
    createRole.mutate(
      { name, label },
      {
        onSuccess: () => {
          setNewRole({ name: '', label: '' });
          setNotice(`已创建角色「${name}」`);
        },
      },
    );
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-44 space-y-1.5">
          <Label htmlFor="role-name">角色标识</Label>
          <Input
            id="role-name"
            value={newRole.name}
            placeholder="如 risk_lead"
            onChange={(event) => setNewRole((s) => ({ ...s, name: event.target.value }))}
          />
        </div>
        <div className="w-44 space-y-1.5">
          <Label htmlFor="role-label">显示名</Label>
          <Input
            id="role-label"
            value={newRole.label}
            placeholder="如 风控负责人"
            onChange={(event) => setNewRole((s) => ({ ...s, label: event.target.value }))}
          />
        </div>
        <Button onClick={handleCreate} disabled={busy}>
          新建角色
        </Button>
        <Button variant="ghost" onClick={discard} disabled={busy}>
          放弃改动
        </Button>
      </div>

      <p className="text-muted-foreground text-xs">
        共 {pages.length} 个页面 key（来自后端注册表），新增页面会自动出现在矩阵中。
      </p>

      {(localError || mutationError) && (
        <p role="alert" className="text-destructive text-sm">
          {localError || errorMessage(mutationError)}
        </p>
      )}
      {notice && <p className="text-stock-down text-sm">{notice}</p>}

      <RoleMatrix
        roles={roles}
        pages={pages}
        draft={draft}
        isDirty={isDirty}
        busy={busy}
        onToggle={toggle}
        onSave={handleSave}
        onReset={handleReset}
        onDelete={handleDelete}
      />
    </div>
  );
}
