/**
 * 能力 → 有序数据源列表编辑器（主备切换）。
 *
 * 列表**顺序即优先级**：第一项为主源，其余按序为备源（主源失败自动降级到下一项）。
 * 保存前二次确认；保存调用 `PUT /api/datasources/prefs`，body 为
 * `{ prefs: { 能力: [源 id 有序] } }`，服务端持久化优先级并在下一轮采集热生效。
 */

import { useEffect, useState } from 'react';
import { ArrowDown, ArrowUp, X } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Select } from '@/components/ui/select';
import type { DatasourceOut } from '@/types/config';
import { ConfirmDialog } from '../components/ConfirmDialog';

export interface CapabilityOrderEditorProps {
  order: Record<string, string[]>;
  sources: readonly DatasourceOut[];
  isAdmin: boolean;
  saving: boolean;
  onSave: (prefs: Record<string, string[]>) => Promise<unknown>;
}

export function CapabilityOrderEditor({
  order,
  sources,
  isAdmin,
  saving,
  onSave,
}: CapabilityOrderEditorProps) {
  const [draft, setDraft] = useState<Record<string, string[]>>(order);
  const [dirty, setDirty] = useState(false);
  const [notice, setNotice] = useState('');
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!dirty) setDraft(order);
  }, [order, dirty]);

  const labelOf = (sourceId: string) =>
    sources.find((item) => item.source_id === sourceId)?.label ?? sourceId;

  const move = (capability: string, index: number, delta: number) => {
    setDraft((previous) => {
      const list = [...(previous[capability] ?? [])];
      const target = index + delta;
      if (target < 0 || target >= list.length) return previous;
      [list[index], list[target]] = [list[target], list[index]];
      return { ...previous, [capability]: list };
    });
    setDirty(true);
  };

  const remove = (capability: string, sourceId: string) => {
    setDraft((previous) => {
      const list = (previous[capability] ?? []).filter((item) => item !== sourceId);
      if (list.length === 0) return previous;
      return { ...previous, [capability]: list };
    });
    setDirty(true);
  };

  const add = (capability: string, sourceId: string) => {
    if (!sourceId) return;
    setDraft((previous) => ({
      ...previous,
      [capability]: [...(previous[capability] ?? []), sourceId],
    }));
    setDirty(true);
  };

  const capabilities = Object.keys(draft).sort();

  const handleSave = async () => {
    setNotice('');
    try {
      await onSave(draft);
      setDirty(false);
      setNotice('主备顺序已保存，下一轮采集即生效');
    } catch {
      setNotice('保存失败，请检查能力与数据源是否合法');
    }
  };

  return (
    <div className="space-y-4">
      <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-400">
        列表顺序即取数优先级：<strong>第一项为主源</strong>，其余按序为备源，主源失败自动降级。
        保存后立即改变线上数据路由（下一轮采集热生效），请谨慎调整。
      </p>

      {capabilities.length === 0 ? (
        <p className="text-muted-foreground text-sm">后端未返回任何能力取数顺序。</p>
      ) : (
        <div className="space-y-3">
          {capabilities.map((capability) => {
            const list = draft[capability] ?? [];
            const available = sources.filter((item) => !list.includes(item.source_id));
            return (
              <div key={capability} className="rounded-md border p-3">
                <div className="mb-2 flex items-center gap-2">
                  <span className="text-sm font-medium">{capability}</span>
                  <Badge variant="outline">主源：{list[0] ? labelOf(list[0]) : '--'}</Badge>
                </div>

                <ol className="space-y-1">
                  {list.map((sourceId, index) => (
                    <li
                      key={sourceId}
                      className="flex items-center gap-2 rounded border px-2 py-1 text-sm"
                    >
                      <Badge variant={index === 0 ? 'default' : 'secondary'}>
                        {index === 0 ? '主' : `备${index}`}
                      </Badge>
                      <span className="min-w-0 flex-1 truncate">
                        {labelOf(sourceId)}
                        <span className="text-muted-foreground ml-1 font-mono text-[11px]">
                          {sourceId}
                        </span>
                      </span>
                      {isAdmin && (
                        <span className="inline-flex gap-0.5">
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`${sourceId} 上移`}
                            disabled={saving || index === 0}
                            onClick={() => move(capability, index, -1)}
                          >
                            <ArrowUp />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`${sourceId} 下移`}
                            disabled={saving || index === list.length - 1}
                            onClick={() => move(capability, index, 1)}
                          >
                            <ArrowDown />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`${sourceId} 移除`}
                            disabled={saving || list.length <= 1}
                            onClick={() => remove(capability, sourceId)}
                          >
                            <X />
                          </Button>
                        </span>
                      )}
                    </li>
                  ))}
                </ol>

                {isAdmin && available.length > 0 && (
                  <div className="mt-2 w-56">
                    <Select
                      aria-label={`为 ${capability} 添加数据源`}
                      value=""
                      disabled={saving}
                      onChange={(event) => add(capability, event.target.value)}
                    >
                      <option value="">添加数据源…</option>
                      {available.map((item) => (
                        <option key={item.source_id} value={item.source_id}>
                          {item.label}（{item.source_id}）
                        </option>
                      ))}
                    </Select>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {isAdmin && (
        <div className="flex flex-wrap items-center gap-3">
          <Button disabled={saving || !dirty} onClick={() => setConfirming(true)}>
            {saving ? '保存中…' : '保存主备顺序'}
          </Button>
          <Button
            variant="ghost"
            disabled={saving || !dirty}
            onClick={() => {
              setDraft(order);
              setDirty(false);
              setNotice('');
            }}
          >
            放弃改动
          </Button>
          {dirty && <span className="text-amber-400 text-xs">有未保存改动</span>}
        </div>
      )}

      {notice && <p className="text-stock-down text-sm">{notice}</p>}

      <ConfirmDialog
        open={confirming}
        title="保存能力主备顺序？"
        description="将覆盖全部能力的数据源优先级并立即生效：主源失败自动降级到备源，下一轮采集即按新顺序取数，无需重启。"
        confirmLabel="确认保存"
        busy={saving}
        onCancel={() => setConfirming(false)}
        onConfirm={() => {
          setConfirming(false);
          void handleSave();
        }}
      />
    </div>
  );
}
