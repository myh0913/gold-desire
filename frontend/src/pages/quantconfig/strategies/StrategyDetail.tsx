/**
 * 策略详情：门控矩阵 + schema 驱动参数表单 + 保存启用（二次确认）+ 启停 + 版本历史。
 *
 * 非 admin 只读：隐藏保存、启停与回滚控件（服务端仍是权威）。
 * 组件由父级以 `key` 重挂载来重置编辑态（切换策略 / 生效版本变化）。
 */

import { useMemo, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { errorMessage } from '@/components/common/StateViews';
import type { StrategyOut } from '@/types/config';
import { useParamDraft } from '../hooks/useParamDraft';
import {
  useRollbackStrategyMutation,
  useSaveStrategyConfigMutation,
  useSetStrategyEnabledMutation,
  useStrategyDiffQuery,
  useStrategyVersionsQuery,
} from '../hooks';
import { parseParamSpecs } from '../lib/paramSchema';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { SchemaParamForm } from '../components/SchemaParamForm';
import { VersionHistoryPanel } from '../components/VersionHistoryPanel';

export interface StrategyDetailProps {
  strategy: StrategyOut;
  isAdmin: boolean;
}

/** 情绪周期门控矩阵（周期态 → 是否允许 + 仓位系数）。 */
function GateMatrixTable({ matrix }: { matrix: StrategyOut['gate_matrix'] }) {
  const entries = Object.entries(matrix ?? {});
  if (entries.length === 0) {
    return <p className="text-muted-foreground text-sm">该策略未声明周期门控矩阵。</p>;
  }
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full border-collapse text-sm">
        <thead className="bg-muted/40 text-muted-foreground">
          <tr>
            <th className="px-3 py-1.5 text-left font-medium">情绪周期态</th>
            <th className="px-3 py-1.5 text-left font-medium">允许开仓</th>
            <th className="px-3 py-1.5 text-right font-medium">仓位系数</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([state, rule]) => (
            <tr key={state} className="border-t">
              <td className="px-3 py-1.5">{state}</td>
              <td className="px-3 py-1.5">
                <Badge variant={rule?.allowed ? 'default' : 'outline'}>
                  {rule?.allowed ? '允许' : '禁止'}
                </Badge>
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums">
                {rule ? rule.position_factor : '--'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function StrategyDetail({ strategy, isAdmin }: StrategyDetailProps) {
  const strategyId = strategy.strategy_id;
  const specs = useMemo(() => parseParamSpecs(strategy.params_schema), [strategy.params_schema]);
  const draft = useParamDraft(specs, strategy.active_params);

  const [note, setNote] = useState('');
  const [notice, setNotice] = useState('');
  const [confirmSave, setConfirmSave] = useState(false);
  const [confirmToggle, setConfirmToggle] = useState(false);
  const [pair, setPair] = useState<{ from: number | null; to: number | null }>({
    from: null,
    to: null,
  });

  const save = useSaveStrategyConfigMutation(strategyId);
  const setEnabled = useSetStrategyEnabledMutation(strategyId);
  const versions = useStrategyVersionsQuery(strategyId);
  const diff = useStrategyDiffQuery(strategyId, pair.from, pair.to);
  const rollback = useRollbackStrategyMutation(strategyId);

  const mutationError = save.error ?? setEnabled.error ?? rollback.error;
  const busy = save.isPending || setEnabled.isPending || rollback.isPending;
  const canSubmit = isAdmin && !busy && draft.dirty && draft.isValid;

  const handleSave = () => {
    setNotice('');
    save.mutate(
      { params: draft.values, note: note.trim() || null },
      {
        onSuccess: (version) => {
          setNote('');
          setNotice(`已保存并启用 v${version.version}，下一轮采集热生效`);
        },
      },
    );
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle>{strategy.label}</CardTitle>
            <Badge variant="outline">{strategy.strategy_id}</Badge>
            <Badge variant="secondary">v{strategy.version}</Badge>
            <Badge variant={strategy.enabled ? 'default' : 'outline'}>
              {strategy.enabled ? '已启用' : '已停用'}
            </Badge>
            {strategy.active_version !== null && (
              <Badge variant="secondary">参数 v{strategy.active_version}</Badge>
            )}
          </div>
          {strategy.description && <CardDescription>{strategy.description}</CardDescription>}
          {strategy.phases.length > 0 && (
            <div className="flex flex-wrap items-center gap-1">
              <span className="text-muted-foreground text-xs">参与阶段</span>
              {strategy.phases.map((phase) => (
                <Badge key={phase} variant="outline">
                  {phase}
                </Badge>
              ))}
            </div>
          )}
        </CardHeader>
        <CardContent className="space-y-4">
          {!isAdmin && (
            <p className="text-muted-foreground rounded-md border px-3 py-2 text-xs">
              当前角色为只读：可查看参数与版本差异，保存 / 启停 / 回滚仅管理员可用。
            </p>
          )}

          <SchemaParamForm
            specs={specs}
            values={draft.values}
            errors={draft.errors}
            disabled={!isAdmin || busy}
            onChange={draft.setValue}
            idPrefix={`strategy-${strategyId}`}
          />

          {isAdmin && (
            <div className="flex flex-wrap items-end gap-3">
              <div className="w-64 space-y-1.5">
                <label className="text-xs" htmlFor={`strategy-note-${strategyId}`}>
                  变更说明
                </label>
                <Input
                  id={`strategy-note-${strategyId}`}
                  value={note}
                  placeholder="如：放宽首阴振幅门槛"
                  onChange={(event) => setNote(event.target.value)}
                />
              </div>
              <Button disabled={!canSubmit} onClick={() => setConfirmSave(true)}>
                {save.isPending ? '保存中…' : '保存并启用'}
              </Button>
              <Button variant="ghost" disabled={busy || !draft.dirty} onClick={() => draft.reset()}>
                放弃改动
              </Button>
              <Button
                variant={strategy.enabled ? 'outline' : 'secondary'}
                disabled={busy}
                onClick={() => {
                  setNotice('');
                  setConfirmToggle(true);
                }}
              >
                {strategy.enabled ? '停用策略' : '启用策略'}
              </Button>
              {draft.dirty && !draft.isValid && (
                <span className="text-destructive text-xs">参数校验未通过，无法保存</span>
              )}
              {draft.dirty && draft.isValid && <span className="text-amber-400 text-xs">有未保存改动</span>}
            </div>
          )}

          {notice && <p className="text-stock-down text-sm">{notice}</p>}
          {mutationError && (
            <p role="alert" className="text-destructive text-sm">
              {errorMessage(mutationError)}
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">情绪周期门控矩阵</CardTitle>
          <CardDescription>
            由策略自身声明（核心不硬编码）：周期态 → 是否允许开仓与仓位系数；未声明的周期态按核心默认回退。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <GateMatrixTable matrix={strategy.gate_matrix} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">版本历史与差异</CardTitle>
          <CardDescription>
            参数版本由后端版本化（draft / active / archived），回滚即以历史内容新建 active 版本。
            版本与收益的对应关系后端未提供接口，故此处不展示版本绩效。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <VersionHistoryPanel
            versions={versions.data?.items ?? []}
            activeVersion={strategy.active_version}
            isAdmin={isAdmin}
            loading={versions.isPending}
            error={versions.error}
            onRetry={() => void versions.refetch()}
            specs={specs}
            from={pair.from}
            to={pair.to}
            onPick={(from, to) => setPair({ from, to })}
            diff={diff.data}
            diffLoading={diff.isFetching}
            diffError={diff.error}
            rollbackPending={rollback.isPending}
            onRollback={(version) => {
              setNotice('');
              rollback.mutate(version, {
                onSuccess: (saved) => setNotice(`已回滚，新建 v${saved.version}`),
              });
            }}
          />
        </CardContent>
      </Card>

      <ConfirmDialog
        open={confirmSave}
        title={`保存并启用 ${strategy.label} 的参数？`}
        description="将以整包参数新建一个 active 版本并立即生效：未填写的参数取 schema 默认值，下一轮采集即按新参数运行，无需重启任何进程。"
        confirmLabel="确认保存并启用"
        busy={save.isPending}
        onCancel={() => setConfirmSave(false)}
        onConfirm={() => {
          setConfirmSave(false);
          handleSave();
        }}
      />

      <ConfirmDialog
        open={confirmToggle}
        title={strategy.enabled ? `停用策略 ${strategy.label}？` : `启用策略 ${strategy.label}？`}
        description={
          strategy.enabled
            ? '停用后该策略不再参与调度，立即生效（不影响已产出的建议）。'
            : '启用后该策略从下一轮调度开始执行。'
        }
        confirmLabel={strategy.enabled ? '确认停用' : '确认启用'}
        busy={setEnabled.isPending}
        onCancel={() => setConfirmToggle(false)}
        onConfirm={() => {
          setConfirmToggle(false);
          setNotice('');
          setEnabled.mutate(!strategy.enabled, {
            onSuccess: () => setNotice(strategy.enabled ? '策略已停用' : '策略已启用'),
          });
        }}
      />
    </div>
  );
}
