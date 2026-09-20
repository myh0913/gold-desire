/**
 * 数据源页（Task 15.3）：注册表 / 健康度 / 连通性探测 / 主备顺序 / 启停 / 采集状态。
 *
 * 读接口需 `quantconfig` 页面权限；写操作（探测、主备顺序、启停、手动采集）
 * 均 admin-only，非 admin 仅展示。启停走二次确认。
 */

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ErrorState, LoadingState, errorMessage } from '@/components/common/StateViews';
import type { DatasourcePingResponse } from '@/types/config';
import {
  useDatasourcesQuery,
  usePingDatasourcesMutation,
  useSaveDatasourcePrefsMutation,
  useSetDatasourceEnabledMutation,
} from '../hooks';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { CapabilityOrderEditor } from './CapabilityOrderEditor';
import { DatasourceTable } from './DatasourceTable';
import { IngestStatusPanel } from './IngestStatusPanel';

export interface DatasourcesPanelProps {
  isAdmin: boolean;
}

export function DatasourcesPanel({ isAdmin }: DatasourcesPanelProps) {
  const sources = useDatasourcesQuery();
  const ping = usePingDatasourcesMutation();
  const savePrefs = useSaveDatasourcePrefsMutation();
  const setEnabled = useSetDatasourceEnabledMutation();

  const [pingResults, setPingResults] = useState<DatasourcePingResponse['results']>();
  const [notice, setNotice] = useState('');
  const [pendingToggle, setPendingToggle] = useState<{ sourceId: string; enabled: boolean } | null>(
    null,
  );

  if (sources.isPending) return <LoadingState title="加载数据源注册表…" />;
  if (sources.error) {
    return <ErrorState error={sources.error} onRetry={() => void sources.refetch()} />;
  }

  const items = sources.data?.items ?? [];
  const order = sources.data?.capability_order ?? {};
  const busy = ping.isPending || savePrefs.isPending || setEnabled.isPending;

  const runPing = (sourceIds: string[]) => {
    setNotice('');
    ping.mutate(sourceIds, {
      onSuccess: (result) =>
        setPingResults((previous) =>
          sourceIds.length === 0 ? result.results : { ...previous, ...result.results },
        ),
    });
  };

  const pendingLabel = items.find((item) => item.source_id === pendingToggle?.sourceId)?.label;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="gap-1">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <CardTitle className="text-sm font-medium">数据源注册表</CardTitle>
              <CardDescription>
                声明能力 + 限频预算 + 最新健康度。探测是唯一允许触达上游的入口，仅 admin 可触发。
              </CardDescription>
            </div>
            {isAdmin && (
              <Button variant="outline" disabled={busy} onClick={() => runPing([])}>
                {ping.isPending ? '探测中…' : '探测全部'}
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {notice && <p className="text-stock-down text-sm">{notice}</p>}
          {ping.error && (
            <p role="alert" className="text-destructive text-sm">
              {errorMessage(ping.error)}
            </p>
          )}
          {setEnabled.error && (
            <p role="alert" className="text-destructive text-sm">
              {errorMessage(setEnabled.error)}
            </p>
          )}
          <DatasourceTable
            sources={items}
            isAdmin={isAdmin}
            busy={busy}
            pingResults={pingResults}
            onPing={(sourceId) => runPing([sourceId])}
            onToggle={(sourceId, enabled) => setPendingToggle({ sourceId, enabled })}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">主备顺序（能力 → 有序数据源）</CardTitle>
          <CardDescription>
            对应 `PUT /api/datasources/prefs`；主源在前，主源失败自动降级到备源。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <CapabilityOrderEditor
            order={order}
            sources={items}
            isAdmin={isAdmin}
            saving={savePrefs.isPending}
            onSave={(prefs) => savePrefs.mutateAsync(prefs)}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">采集状态</CardTitle>
          <CardDescription>任务明细、能力健康度与手动触发。</CardDescription>
        </CardHeader>
        <CardContent>
          <IngestStatusPanel isAdmin={isAdmin} />
        </CardContent>
      </Card>

      <ConfirmDialog
        open={pendingToggle !== null}
        title={
          pendingToggle?.enabled
            ? `启用数据源 ${pendingLabel ?? pendingToggle?.sourceId}？`
            : `停用数据源 ${pendingLabel ?? pendingToggle?.sourceId}？`
        }
        description={
          pendingToggle?.enabled
            ? '启用后该源立即重新参与各能力的主备取数（按当前优先级）。'
            : '停用后该源立即退出所有能力的主备取数；若它是某能力唯一可用源，该能力将采集失败。'
        }
        confirmLabel={pendingToggle?.enabled ? '确认启用' : '确认停用'}
        busy={setEnabled.isPending}
        onCancel={() => setPendingToggle(null)}
        onConfirm={() => {
          if (pendingToggle) {
            setNotice('');
            setEnabled.mutate(pendingToggle, {
              onSuccess: ({ source_id, enabled }) =>
                setNotice(`${source_id} 已${enabled ? '启用' : '停用'}`),
            });
          }
          setPendingToggle(null);
        }}
      />
    </div>
  );
}
