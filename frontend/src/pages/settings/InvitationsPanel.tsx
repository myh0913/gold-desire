/**
 * 邀请码面板（仅管理员）：列表、创建、吊销。
 */

import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { DataTable, type DataTableColumn } from '@/components/common/DataTable';
import { errorMessage } from '@/components/common/StateViews';
import { fmtDateTime, isPastSh } from '@/lib/time';
import {
  useCreateInvitationMutation,
  useInvitationsQuery,
  useRevokeInvitationMutation,
  useRolesQuery,
} from '@/lib/queries';
import type { InvitationOut } from '@/types';

type InvitationStatus = 'active' | 'used' | 'revoked' | 'expired';

const STATUS_META: Record<InvitationStatus, { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline' }> = {
  active: { label: '可用', variant: 'default' },
  used: { label: '已使用', variant: 'secondary' },
  revoked: { label: '已吊销', variant: 'destructive' },
  expired: { label: '已过期', variant: 'outline' },
};

function statusOf(invitation: InvitationOut): InvitationStatus {
  if (invitation.revoked) return 'revoked';
  if (invitation.used_by) return 'used';
  if (invitation.expires_at && isPastSh(invitation.expires_at)) {
    return 'expired';
  }
  return 'active';
}

export function InvitationsPanel() {
  const invitations = useInvitationsQuery();
  const rolesQuery = useRolesQuery();
  const createInvitation = useCreateInvitationMutation();
  const revokeInvitation = useRevokeInvitationMutation();

  const [form, setForm] = useState({ role: 'viewer', days: 7 });
  const [notice, setNotice] = useState('');

  const roleOptions = rolesQuery.data ?? [];
  const busy = createInvitation.isPending || revokeInvitation.isPending;
  const mutationError = createInvitation.error ?? revokeInvitation.error;

  const handleCreate = () => {
    setNotice('');
    createInvitation.mutate(
      { role: form.role, expires_in_days: form.days },
      { onSuccess: (invitation) => setNotice(`已生成邀请码：${invitation.code}`) },
    );
  };

  const handleRevoke = (invitation: InvitationOut) => {
    if (!window.confirm(`确认吊销邀请码「${invitation.code}」？`)) return;
    setNotice('');
    revokeInvitation.mutate(invitation.code, {
      onSuccess: () => setNotice(`已吊销邀请码「${invitation.code}」`),
    });
  };

  const columns: DataTableColumn<InvitationOut>[] = [
    {
      key: 'code',
      header: '邀请码',
      render: (row) => <span className="font-mono text-xs">{row.code}</span>,
    },
    { key: 'role', header: '角色', render: (row) => row.role },
    {
      key: 'expires_at',
      header: '有效期至',
      render: (row) => (
        <span className="tabular-nums">{row.expires_at ? fmtDateTime(row.expires_at) : '--'}</span>
      ),
    },
    { key: 'used_by', header: '使用者', render: (row) => row.used_by ?? '--' },
    {
      key: 'status',
      header: '状态',
      render: (row) => {
        const status = statusOf(row);
        return <Badge variant={STATUS_META[status].variant}>{STATUS_META[status].label}</Badge>;
      },
    },
    {
      key: 'actions',
      header: '操作',
      align: 'right',
      render: (row) => (
        <Button
          size="sm"
          variant="destructive"
          disabled={busy || row.revoked || row.used_by !== null}
          onClick={() => handleRevoke(row)}
        >
          吊销
        </Button>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-36 space-y-1.5">
          <Label htmlFor="invite-role">角色</Label>
          <Select
            id="invite-role"
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
        <div className="w-32 space-y-1.5">
          <Label htmlFor="invite-days">有效天数</Label>
          <Input
            id="invite-days"
            type="number"
            min={1}
            max={365}
            value={form.days}
            onChange={(event) =>
              setForm((s) => ({ ...s, days: Number(event.target.value) || 1 }))
            }
          />
        </div>
        <Button onClick={handleCreate} disabled={busy}>
          生成邀请码
        </Button>
      </div>

      {mutationError && (
        <p role="alert" className="text-destructive text-sm">
          {errorMessage(mutationError)}
        </p>
      )}
      {notice && <p className="text-stock-down text-sm">{notice}</p>}

      <DataTable
        columns={columns}
        rows={invitations.data}
        rowKey={(row) => row.code}
        loading={invitations.isPending}
        error={invitations.error}
        onRetry={() => void invitations.refetch()}
        emptyTitle="暂无邀请码"
      />
    </div>
  );
}
