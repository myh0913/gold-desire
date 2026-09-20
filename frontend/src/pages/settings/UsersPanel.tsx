/**
 * 用户管理面板（仅管理员）：列表、创建、改角色/启停、重置密码、删除。
 */

import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { DataTable, type DataTableColumn } from '@/components/common/DataTable';
import { errorMessage } from '@/components/common/StateViews';
import { fmtDateTime } from '@/lib/time';
import {
  useCreateUserMutation,
  useDeleteUserMutation,
  useResetPasswordMutation,
  useRolesQuery,
  useUpdateUserMutation,
  useUsersQuery,
} from '@/lib/queries';
import type { UserOut } from '@/types';

function formatStamp(value: string | null): string {
  return value ? fmtDateTime(value) : '--';
}

export function UsersPanel() {
  const users = useUsersQuery();
  const rolesQuery = useRolesQuery();
  const createUser = useCreateUserMutation();
  const updateUser = useUpdateUserMutation();
  const resetPassword = useResetPasswordMutation();
  const deleteUser = useDeleteUserMutation();

  const [form, setForm] = useState({ username: '', password: '', role: 'viewer' });
  const [notice, setNotice] = useState('');
  const [localError, setLocalError] = useState('');

  const roleOptions = rolesQuery.data ?? [];
  const busy =
    createUser.isPending || updateUser.isPending || resetPassword.isPending || deleteUser.isPending;
  const mutationError =
    createUser.error ?? updateUser.error ?? resetPassword.error ?? deleteUser.error;

  const handleCreate = () => {
    setLocalError('');
    setNotice('');
    if (form.username.trim().length < 3) {
      setLocalError('用户名至少 3 个字符');
      return;
    }
    if (form.password.length < 8) {
      setLocalError('密码至少 8 个字符');
      return;
    }
    createUser.mutate(
      { username: form.username.trim(), password: form.password, role: form.role },
      {
        onSuccess: () => {
          setForm({ username: '', password: '', role: form.role });
          setNotice('用户已创建');
        },
      },
    );
  };

  const handleResetPassword = (user: UserOut) => {
    const password = window.prompt(`为「${user.username}」设置新密码（至少 8 位）`);
    if (!password) return;
    if (password.length < 8) {
      setLocalError('密码至少 8 个字符');
      return;
    }
    resetPassword.mutate(
      { username: user.username, body: { password } },
      { onSuccess: () => setNotice(`已重置「${user.username}」的密码`) },
    );
  };

  const handleDelete = (user: UserOut) => {
    if (!window.confirm(`确认删除用户「${user.username}」？该操作不可撤销。`)) return;
    deleteUser.mutate(user.username, { onSuccess: () => setNotice(`已删除「${user.username}」`) });
  };

  const columns: DataTableColumn<UserOut>[] = [
    { key: 'username', header: '用户名', render: (row) => row.username },
    {
      key: 'role',
      header: '角色',
      render: (row) => (
        <Select
          aria-label={`${row.username} 的角色`}
          className="h-8 w-32"
          value={row.role}
          disabled={busy}
          onChange={(event) =>
            updateUser.mutate({ username: row.username, body: { role: event.target.value } })
          }
        >
          {roleOptions.map((role) => (
            <option key={role.name} value={role.name}>
              {role.label}
            </option>
          ))}
          {!roleOptions.some((role) => role.name === row.role) && (
            <option value={row.role}>{row.role}</option>
          )}
        </Select>
      ),
    },
    {
      key: 'enabled',
      header: '状态',
      render: (row) => (
        <Button
          variant={row.enabled ? 'outline' : 'secondary'}
          size="sm"
          disabled={busy}
          onClick={() =>
            updateUser.mutate({ username: row.username, body: { enabled: !row.enabled } })
          }
        >
          {row.enabled ? '已启用' : '已停用'}
        </Button>
      ),
    },
    {
      key: 'last_login_at',
      header: '最近登录',
      render: (row) => <span className="tabular-nums">{formatStamp(row.last_login_at)}</span>,
    },
    {
      key: 'created_at',
      header: '创建时间',
      render: (row) => <span className="tabular-nums">{formatStamp(row.created_at)}</span>,
    },
    {
      key: 'actions',
      header: '操作',
      align: 'right',
      render: (row) => (
        <div className="inline-flex gap-1">
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => handleResetPassword(row)}>
            重置密码
          </Button>
          <Button size="sm" variant="destructive" disabled={busy} onClick={() => handleDelete(row)}>
            删除
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-40 space-y-1.5">
          <Label htmlFor="new-user-name">用户名</Label>
          <Input
            id="new-user-name"
            value={form.username}
            onChange={(event) => setForm((s) => ({ ...s, username: event.target.value }))}
          />
        </div>
        <div className="w-44 space-y-1.5">
          <Label htmlFor="new-user-password">初始密码</Label>
          <Input
            id="new-user-password"
            type="password"
            value={form.password}
            onChange={(event) => setForm((s) => ({ ...s, password: event.target.value }))}
          />
        </div>
        <div className="w-36 space-y-1.5">
          <Label htmlFor="new-user-role">角色</Label>
          <Select
            id="new-user-role"
            value={form.role}
            onChange={(event) => setForm((s) => ({ ...s, role: event.target.value }))}
          >
            {roleOptions.map((role) => (
              <option key={role.name} value={role.name}>
                {role.label}
              </option>
            ))}
          </Select>
        </div>
        <Button onClick={handleCreate} disabled={busy}>
          创建用户
        </Button>
        <Badge variant="secondary">共 {users.data?.length ?? 0} 人</Badge>
      </div>

      {(localError || mutationError) && (
        <p role="alert" className="text-destructive text-sm">
          {localError || errorMessage(mutationError)}
        </p>
      )}
      {notice && <p className="text-stock-down text-sm">{notice}</p>}

      <DataTable
        columns={columns}
        rows={users.data}
        rowKey={(row) => row.username}
        loading={users.isPending}
        error={users.error}
        onRetry={() => void users.refetch()}
        emptyTitle="暂无用户"
      />
    </div>
  );
}
