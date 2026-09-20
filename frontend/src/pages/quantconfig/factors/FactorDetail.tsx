/**
 * 因子详情：schema 驱动参数表单 + 保存启用（二次确认，警示影响下一轮选股）+ 版本历史 + 有效性。
 *
 * enum 参数的后端 schema 未下发候选值，候选取自因子 `buckets[].equals`；
 * 取不到时 SchemaParamForm 退化为自由文本输入。非 admin 只读（隐藏保存与回滚控件）。
 */

import { useMemo, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { errorMessage } from '@/components/common/StateViews';
import type { FactorOut } from '@/types/config';
import { useParamDraft } from '../hooks/useParamDraft';
import {
  useFactorDiffQuery,
  useFactorEffectivenessQuery,
  useFactorVersionsQuery,
  useRollbackFactorMutation,
  useSaveFactorConfigMutation,
} from '../hooks';
import { deriveEnumOptions, parseParamSpecs } from '../lib/paramSchema';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { SchemaParamForm } from '../components/SchemaParamForm';
import { VersionHistoryPanel } from '../components/VersionHistoryPanel';
import { FactorEffectiveness } from './FactorEffectiveness';

export interface FactorDetailProps {
  factor: FactorOut;
  isAdmin: boolean;
}

export function FactorDetail({ factor, isAdmin }: FactorDetailProps) {
  const factorId = factor.factor_id;
  const specs = useMemo(() => parseParamSpecs(factor.params_schema), [factor.params_schema]);
  const enumOptions = useMemo(() => {
    const buckets = factor.buckets.length > 0 ? factor.buckets : factor.params_schema.buckets;
    const options = deriveEnumOptions(buckets);
    const map: Record<string, string[]> = {};
    for (const spec of specs) {
      if (spec.type === 'enum' && options.length > 0) map[spec.key] = options;
    }
    return map;
  }, [factor.buckets, factor.params_schema.buckets, specs]);

  const draft = useParamDraft(specs, factor.active_params, enumOptions);
  const [note, setNote] = useState('');
  const [notice, setNotice] = useState('');
  const [confirmSave, setConfirmSave] = useState(false);
  const [pair, setPair] = useState<{ from: number | null; to: number | null }>({
    from: null,
    to: null,
  });

  const save = useSaveFactorConfigMutation(factorId);
  const versions = useFactorVersionsQuery(factorId);
  const diff = useFactorDiffQuery(factorId, pair.from, pair.to);
  const rollback = useRollbackFactorMutation(factorId);
  const effectiveness = useFactorEffectivenessQuery(factorId);

  const mutationError = save.error ?? rollback.error;
  const busy = save.isPending || rollback.isPending;
  const canSubmit = isAdmin && !busy && draft.dirty && draft.isValid;

  const handleSave = () => {
    setNotice('');
    save.mutate(
      { params: draft.values, note: note.trim() || null },
      {
        onSuccess: (version) => {
          setNote('');
          setNotice(`已保存并启用 v${version.version}，下一轮选股即按新参数执行`);
        },
      },
    );
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle>{factor.label}</CardTitle>
            <Badge variant="outline">{factor.factor_id}</Badge>
            <Badge variant="secondary">{factor.category}</Badge>
            <Badge variant={factor.enabled ? 'default' : 'outline'}>
              {factor.enabled ? '已启用' : '已停用'}
            </Badge>
            {factor.active_version !== null && (
              <Badge variant="secondary">参数 v{factor.active_version}</Badge>
            )}
          </div>
          {factor.description && <CardDescription>{factor.description}</CardDescription>}
        </CardHeader>
        <CardContent className="space-y-4">
          {!isAdmin && (
            <p className="text-muted-foreground rounded-md border px-3 py-2 text-xs">
              当前角色为只读：可查看参数、版本差异与有效性统计，保存 / 回滚仅管理员可用。
            </p>
          )}

          <SchemaParamForm
            specs={specs}
            values={draft.values}
            errors={draft.errors}
            enumOptions={enumOptions}
            disabled={!isAdmin || busy}
            onChange={draft.setValue}
            idPrefix={`factor-${factorId}`}
          />

          {isAdmin && (
            <div className="flex flex-wrap items-end gap-3">
              <div className="w-64 space-y-1.5">
                <label className="text-xs" htmlFor={`factor-note-${factorId}`}>
                  变更说明
                </label>
                <Input
                  id={`factor-note-${factorId}`}
                  value={note}
                  placeholder="如：首阴振幅门槛 8% → 7%"
                  onChange={(event) => setNote(event.target.value)}
                />
              </div>
              <Button disabled={!canSubmit} onClick={() => setConfirmSave(true)}>
                {save.isPending ? '保存中…' : '保存并启用'}
              </Button>
              <Button variant="ghost" disabled={busy || !draft.dirty} onClick={() => draft.reset()}>
                放弃改动
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
          <CardTitle className="text-sm font-medium">有效性统计（按档位 × A/B/C 分段）</CardTitle>
          <CardDescription>
            前向收益口径：D+1 开盘买入 → D+2 收盘了结的毛收益（不含止损）。
            每档位同时展开 A / B / C 三段，禁止只看聚合。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <FactorEffectiveness
            data={
              effectiveness.data ?? {
                factor_id: factorId,
                params_version: '--',
                cuts: null,
                sample_count: 0,
                buckets: [],
              }
            }
            loading={effectiveness.isPending}
            error={effectiveness.error}
            onRetry={() => void effectiveness.refetch()}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">版本历史与差异</CardTitle>
        </CardHeader>
        <CardContent>
          <VersionHistoryPanel
            versions={versions.data?.items ?? []}
            activeVersion={factor.active_version}
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
        title={`保存并启用因子 ${factor.label} 的参数？`}
        description="阈值改动会影响下一轮选股：新参数将立即作为 active 版本生效，下一轮选股即按新阈值分档，无需改代码、无需重启。请先对照下方 A/B/C 分段统计确认改动影响。"
        confirmLabel="确认保存并启用"
        busy={save.isPending}
        onCancel={() => setConfirmSave(false)}
        onConfirm={() => {
          setConfirmSave(false);
          handleSave();
        }}
      />
    </div>
  );
}
