/**
 * 连接面板：后端地址、实时通道地址与状态、健康检查。
 *
 * WS 连接由本面板显式触发（不在应用启动时自动连接），
 * 避免后端尚未提供 `/ws` 时产生无意义的重连风暴。
 */

import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ConnectionStatus } from '@/components/common/ConnectionStatus';
import { ErrorState } from '@/components/common/StateViews';
import { getApiBaseUrl, getWsUrl, request } from '@/lib/api';
import { queryKeys } from '@/lib/queries';
import { getWsClient } from '@/lib/ws';
import type { HealthResponse } from '@/types';

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b py-2 text-sm last:border-b-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="truncate font-mono text-xs">{value}</span>
    </div>
  );
}

export function ConnectionPanel() {
  const health = useQuery<HealthResponse>({
    queryKey: queryKeys.health,
    queryFn: ({ signal }) => request<HealthResponse>('/health', { signal }),
    retry: false,
  });

  const ws = getWsClient();

  return (
    <div className="space-y-4">
      <div className="rounded-md border px-4 py-2">
        <InfoRow label="API 基础地址" value={getApiBaseUrl()} />
        <InfoRow label="WebSocket 地址" value={getWsUrl()} />
        <div className="flex items-center justify-between gap-4 py-2 text-sm">
          <span className="text-muted-foreground">实时通道</span>
          <ConnectionStatus />
        </div>
        <div className="flex items-center justify-between gap-4 py-2 text-sm">
          <span className="text-muted-foreground">后端健康</span>
          {health.isPending ? (
            <Badge variant="secondary">检测中…</Badge>
          ) : health.error ? (
            <Badge variant="destructive">不可用</Badge>
          ) : (
            <Badge variant="default">
              {(health.data?.status === 'ok' ? '正常' : health.data?.status) ??
                '--'}{' '}
              · {health.data?.service}
            </Badge>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => ws.connect()}>
          连接实时通道
        </Button>
        <Button variant="ghost" onClick={() => ws.disconnect()}>
          断开
        </Button>
        <Button variant="ghost" onClick={() => void health.refetch()}>
          重新检测健康
        </Button>
      </div>

      {health.error && <ErrorState title="健康检查失败" error={health.error} onRetry={() => void health.refetch()} />}
    </div>
  );
}
