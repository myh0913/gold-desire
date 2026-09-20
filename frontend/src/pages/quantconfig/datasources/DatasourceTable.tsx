/**
 * 数据源列表：类型 / 声明能力 / 限频 / 优先级 / 启停 / 健康度 / 连通性探测结果。
 *
 * 健康度来自 `GET /api/datasources` 的 `health`（按能力分组：ok / latency_ms / detail）；
 * 探测结果来自 `POST /api/datasources/ping`（按能力分组：ok / latency_ms / error）。
 */

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { fmtDateTime } from '@/lib/time';
import { cn } from '@/lib/utils';
import type { DatasourceHealthItem, DatasourceOut, DatasourcePingResponse } from '@/types/config';

export interface DatasourceTableProps {
  sources: readonly DatasourceOut[];
  isAdmin: boolean;
  busy: boolean;
  pingResults?: DatasourcePingResponse['results'];
  onPing: (sourceId: string) => void;
  onToggle: (sourceId: string, enabled: boolean) => void;
}

type HealthEntry = { capability: string; item: DatasourceHealthItem };

function healthEntries(health: Record<string, DatasourceHealthItem> | undefined): HealthEntry[] {
  return Object.entries(health ?? {}).map(([capability, item]) => ({ capability, item }));
}

function HealthCell({ entries }: { entries: HealthEntry[] }) {
  if (entries.length === 0) {
    return <span className="text-muted-foreground text-xs">尚无记录</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {entries.map(({ capability, item }) => (
        <span
          key={capability}
          title={item.detail ? JSON.stringify(item.detail) : undefined}
          className={cn(
            'rounded border px-1.5 py-0.5 font-mono text-[10px]',
            item.ok
              ? 'border-stock-down/40 text-stock-down'
              : 'border-destructive/40 text-destructive',
          )}
        >
          {capability} {item.ok ? 'ok' : 'fail'}
          {typeof item.latency_ms === 'number' ? ` · ${item.latency_ms}ms` : ''}
        </span>
      ))}
    </div>
  );
}

function PingCell({ result }: { result?: Record<string, { ok: boolean; latency_ms: number | null; error: string | null }> }) {
  if (!result) return <span className="text-muted-foreground text-xs">未探测</span>;
  return (
    <div className="space-y-0.5">
      {Object.entries(result).map(([capability, item]) => (
        <div key={capability} className="font-mono text-[10px]">
          <span className={item.ok ? 'text-stock-down' : 'text-destructive'}>
            {capability} {item.ok ? 'ok' : 'fail'}
          </span>
          {item.latency_ms !== null && <span className="text-muted-foreground"> · {item.latency_ms}ms</span>}
          {item.error && <div className="text-destructive break-all">{item.error}</div>}
        </div>
      ))}
    </div>
  );
}

export function DatasourceTable({
  sources,
  isAdmin,
  busy,
  pingResults,
  onPing,
  onToggle,
}: DatasourceTableProps) {
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full border-collapse text-sm">
        <thead className="bg-muted/40 text-muted-foreground">
          <tr>
            <th className="px-3 py-2 text-left font-medium">数据源</th>
            <th className="px-3 py-2 text-left font-medium">类型</th>
            <th className="px-3 py-2 text-left font-medium">声明能力</th>
            <th className="px-3 py-2 text-right font-medium">限频/分</th>
            <th className="px-3 py-2 text-right font-medium">优先级</th>
            <th className="px-3 py-2 text-left font-medium">状态</th>
            <th className="px-3 py-2 text-left font-medium">健康度</th>
            <th className="px-3 py-2 text-left font-medium">最近探测</th>
            <th className="px-3 py-2 text-left font-medium">探测结果</th>
            {isAdmin && <th className="px-3 py-2 text-right font-medium">操作</th>}
          </tr>
        </thead>
        <tbody>
          {sources.map((source) => (
            <tr key={source.source_id} className="border-t align-top">
              <td className="px-3 py-2">
                <div className="font-medium">{source.label}</div>
                <div className="text-muted-foreground font-mono text-[11px]">{source.source_id}</div>
              </td>
              <td className="px-3 py-2">
                <Badge variant="outline">{source.kind}</Badge>
              </td>
              <td className="px-3 py-2">
                <div className="flex flex-wrap gap-1">
                  {source.capabilities.length === 0 ? (
                    <span className="text-muted-foreground text-xs">--</span>
                  ) : (
                    source.capabilities.map((capability) => (
                      <Badge key={capability} variant="secondary">
                        {capability}
                      </Badge>
                    ))
                  )}
                </div>
              </td>
              <td className="px-3 py-2 text-right tabular-nums">{source.rate_limit_per_min}</td>
              <td className="px-3 py-2 text-right tabular-nums">{source.priority}</td>
              <td className="px-3 py-2">
                <Badge variant={source.enabled ? 'default' : 'outline'}>
                  {source.enabled ? '启用' : '停用'}
                </Badge>
              </td>
              <td className="px-3 py-2">
                <HealthCell entries={healthEntries(source.health)} />
              </td>
              <td className="px-3 py-2 tabular-nums">
                {source.last_check ? fmtDateTime(source.last_check) : '--'}
              </td>
              <td className="px-3 py-2">
                <PingCell result={pingResults?.[source.source_id]} />
              </td>
              {isAdmin && (
                <td className="px-3 py-2 text-right">
                  <div className="inline-flex gap-1">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() => onPing(source.source_id)}
                    >
                      探测
                    </Button>
                    <Button
                      variant={source.enabled ? 'ghost' : 'secondary'}
                      size="sm"
                      disabled={busy}
                      onClick={() => onToggle(source.source_id, !source.enabled)}
                    >
                      {source.enabled ? '停用' : '启用'}
                    </Button>
                  </div>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
