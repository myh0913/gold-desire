/**
 * 版本历史面板：版本列表 + 任选两版对比 + 一键回滚（admin）。
 *
 * 回滚 = 以历史内容新建一个 active 版本（后端语义），故需二次确认。
 * 非 admin 用户只看到列表与差异，不渲染任何写操作按钮。
 */

import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Select } from '@/components/ui/select';
import { ErrorState, LoadingState } from '@/components/common/StateViews';
import { fmtDateTime } from '@/lib/time';
import type { ConfigDiffOut, ConfigVersionOut, ParamSpec } from '@/types/config';
import { ConfirmDialog } from './ConfirmDialog';
import { VersionDiffView } from './VersionDiffView';

export interface VersionHistoryPanelProps {
  versions: ConfigVersionOut[];
  activeVersion: number | null;
  isAdmin: boolean;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
  specs?: readonly ParamSpec[];
  from: number | null;
  to: number | null;
  onPick: (from: number, to: number) => void;
  diff?: ConfigDiffOut;
  diffLoading: boolean;
  diffError: unknown;
  rollbackPending: boolean;
  onRollback: (version: number) => void;
}

const STATUS_VARIANT: Record<string, 'default' | 'secondary' | 'outline'> = {
  active: 'default',
  draft: 'secondary',
  archived: 'outline',
};

export function VersionHistoryPanel({
  versions,
  activeVersion,
  isAdmin,
  loading = false,
  error,
  onRetry,
  specs,
  from,
  to,
  onPick,
  diff,
  diffLoading,
  diffError,
  rollbackPending,
  onRollback,
}: VersionHistoryPanelProps) {
  const [pendingVersion, setPendingVersion] = useState<number | null>(null);

  if (loading) return <LoadingState title="加载版本历史…" />;
  if (error !== undefined && error !== null) {
    return <ErrorState error={error} onRetry={onRetry} />;
  }
  if (versions.length === 0) {
    return <p className="text-muted-foreground text-sm">暂无历史版本（当前使用代码默认参数）。</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-40 space-y-1.5">
          <label className="text-muted-foreground text-xs" htmlFor="version-diff-from">
            对比基准（from）
          </label>
          <Select
            id="version-diff-from"
            className="h-8"
            value={from === null ? '' : String(from)}
            onChange={(event) => onPick(Number(event.target.value), to ?? versions[0].version)}
          >
            <option value="">选择版本</option>
            {versions.map((item) => (
              <option key={item.version} value={item.version}>
                v{item.version}
              </option>
            ))}
          </Select>
        </div>
        <div className="w-40 space-y-1.5">
          <label className="text-muted-foreground text-xs" htmlFor="version-diff-to">
            对比目标（to）
          </label>
          <Select
            id="version-diff-to"
            className="h-8"
            value={to === null ? '' : String(to)}
            onChange={(event) => onPick(from ?? versions[0].version, Number(event.target.value))}
          >
            <option value="">选择版本</option>
            {versions.map((item) => (
              <option key={item.version} value={item.version}>
                v{item.version}
              </option>
            ))}
          </Select>
        </div>
        <p className="text-muted-foreground text-xs">
          回滚会以历史内容**新建**一个 active 版本，不修改既有版本。
        </p>
      </div>

      <div className="rounded-md border p-3">
        {from === null || to === null ? (
          <p className="text-muted-foreground text-sm">选择两个版本后显示参数差异。</p>
        ) : diffLoading ? (
          <LoadingState title="计算差异…" />
        ) : diffError !== undefined && diffError !== null ? (
          <ErrorState error={diffError} />
        ) : diff ? (
          <VersionDiffView diff={diff} specs={specs} />
        ) : null}
      </div>

      <div className="overflow-x-auto rounded-md border">
        <table className="w-full border-collapse text-sm">
          <thead className="bg-muted/40 text-muted-foreground">
            <tr>
              <th className="px-3 py-2 text-left font-medium">版本</th>
              <th className="px-3 py-2 text-left font-medium">状态</th>
              <th className="px-3 py-2 text-left font-medium">变更说明</th>
              <th className="px-3 py-2 text-left font-medium">操作人</th>
              <th className="px-3 py-2 text-left font-medium">创建时间</th>
              {isAdmin && <th className="px-3 py-2 text-right font-medium">操作</th>}
            </tr>
          </thead>
          <tbody>
            {versions.map((item) => (
              <tr key={item.version} className="border-t">
                <td className="px-3 py-2 tabular-nums">v{item.version}</td>
                <td className="px-3 py-2">
                  <Badge variant={STATUS_VARIANT[item.status] ?? 'outline'}>{item.status}</Badge>
                  {activeVersion === item.version && (
                    <span className="text-muted-foreground ml-2 text-xs">生效中</span>
                  )}
                </td>
                <td className="px-3 py-2">{item.note ?? '--'}</td>
                <td className="px-3 py-2">{item.created_by ?? '--'}</td>
                <td className="px-3 py-2 tabular-nums">
                  {item.created_at ? fmtDateTime(item.created_at) : '--'}
                </td>
                {isAdmin && (
                  <td className="px-3 py-2 text-right">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={rollbackPending || activeVersion === item.version}
                      onClick={() => setPendingVersion(item.version)}
                    >
                      回滚到此版本
                    </Button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ConfirmDialog
        open={pendingVersion !== null}
        title={`回滚到 v${pendingVersion ?? ''}？`}
        description="系统会以该版本的参数内容新建一个 active 版本；下一轮采集即热生效，无需重启。"
        confirmLabel="确认回滚"
        busy={rollbackPending}
        onCancel={() => setPendingVersion(null)}
        onConfirm={() => {
          if (pendingVersion !== null) onRollback(pendingVersion);
          setPendingVersion(null);
        }}
      />
    </div>
  );
}
