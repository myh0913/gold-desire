/**
 * 个人资料面板：用户名 / 角色只读 + 账户本金。
 *
 * 本金用于建议页「参考股数」（本金 × 建议仓位 ÷ 买点价，向下取整到 100 股），
 * 走 `PATCH /auth/me`（仅限本人；null = 清除），服务端仍校验 self_only。
 */

import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useAuth } from '@/hooks/useAuth';
import { api, ApiError } from '@/lib/api';
import { queryKeys } from '@/lib/queries';
import type { MeResponse } from '@/types';

export function ProfilePanel() {
  const { me } = useAuth();
  const client = useQueryClient();
  const capitalYuan = me?.user.capital_yuan ?? null;
  const [capital, setCapital] = useState(capitalYuan != null ? String(capitalYuan) : '');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 会话刷新（登录/他处更新）后同步输入框
  useEffect(() => {
    setCapital(capitalYuan != null ? String(capitalYuan) : '');
  }, [capitalYuan]);

  const mutation = useMutation({
    mutationFn: (body: { capital_yuan: number | null }) => api.auth.updateMe(body),
    onSuccess: (user) => {
      if (me) client.setQueryData<MeResponse>(queryKeys.me, { ...me, user });
      setError(null);
      setMessage('已保存');
    },
    onError: (err) => {
      setMessage(null);
      setError(err instanceof ApiError ? err.message : '保存失败，请稍后重试');
    },
  });

  const save = () => {
    const trimmed = capital.trim();
    if (trimmed === '') {
      mutation.mutate({ capital_yuan: null });
      return;
    }
    const value = Number(trimmed);
    if (!Number.isFinite(value) || value < 0) {
      setMessage(null);
      setError('本金需为非负数字（元）');
      return;
    }
    mutation.mutate({ capital_yuan: value });
  };

  if (!me) return null;

  return (
    <div className="max-w-xl space-y-4" data-testid="profile-panel">
      <div className="rounded-md border px-4 py-2 text-sm">
        <div className="flex items-center justify-between gap-4 border-b py-2">
          <span className="text-muted-foreground">用户名</span>
          <span>{me.user.username}</span>
        </div>
        <div className="flex items-center justify-between gap-4 py-2">
          <span className="text-muted-foreground">角色</span>
          <span>{me.role}</span>
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="profile-capital">账户本金（元）</Label>
        <div className="flex gap-2">
          <Input
            id="profile-capital"
            inputMode="decimal"
            placeholder="如 200000；留空保存即清除"
            value={capital}
            onChange={(event) => setCapital(event.target.value)}
          />
          <Button onClick={save} disabled={mutation.isPending}>
            保存
          </Button>
        </div>
        <p className="text-muted-foreground text-xs">
          设置后，量化选股页的建议卡按「本金 × 建议仓位 ÷ 买点价」直接给出参考股数；未设置则不显示股数。
        </p>
      </div>

      {message && <p className="text-stock-up text-sm">{message}</p>}
      {error && <p className="text-stock-down text-sm">{error}</p>}
    </div>
  );
}
