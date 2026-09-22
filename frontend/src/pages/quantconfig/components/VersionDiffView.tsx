/**
 * 版本差异视图：渲染 `added` / `removed` / `changed` 三组参数。
 *
 * `changed` 的值为 `[旧值, 新值]`（后端 `ConfigDiffOut.changed`）。
 */

import { Badge } from '@/components/ui/badge';
import type { ConfigDiffOut, ParamSpec } from '@/types/config';
import { formatParamValue } from '../lib/paramSchema';

export interface VersionDiffViewProps {
  diff: ConfigDiffOut;
  /** 参数声明（用于把小数口径按类型格式化；缺省时原样输出） */
  specs?: readonly ParamSpec[];
}

function renderValue(specs: readonly ParamSpec[] | undefined, key: string, value: unknown): string {
  const spec = specs?.find((item) => item.key === key);
  return spec ? formatParamValue(spec, value) : String(value ?? '--');
}

/** 参数 key → 中文 label（无声明或无 label 时回退原 key）。 */
function renderKey(specs: readonly ParamSpec[] | undefined, key: string): string {
  return specs?.find((item) => item.key === key)?.label ?? key;
}

export function VersionDiffView({ diff, specs }: VersionDiffViewProps) {
  const added = Object.entries(diff.added);
  const removed = Object.entries(diff.removed);
  const changed = Object.entries(diff.changed);
  const empty = added.length === 0 && removed.length === 0 && changed.length === 0;

  if (empty) {
    return (
      <p className="text-muted-foreground text-sm">
        v{diff.from_version} 与 v{diff.to_version} 参数完全一致。
      </p>
    );
  }

  return (
    <div className="space-y-3 text-sm">
      <p className="text-muted-foreground text-xs">
        对比 v{diff.from_version} → v{diff.to_version}
      </p>

      {changed.length > 0 && (
        <div className="space-y-1">
          <div className="font-medium">变更（{changed.length}）</div>
          <ul className="space-y-1">
            {changed.map(([key, pair]) => (
              <li key={key} className="flex flex-wrap items-center gap-2 font-mono text-xs">
                <span className="text-muted-foreground">{renderKey(specs, key)}</span>
                <span className="text-destructive line-through">
                  {renderValue(specs, key, pair?.[0])}
                </span>
                <span aria-hidden="true">→</span>
                <span className="text-stock-down">{renderValue(specs, key, pair?.[1])}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {added.length > 0 && (
        <div className="space-y-1">
          <div className="font-medium">新增（{added.length}）</div>
          <ul className="space-y-1">
            {added.map(([key, value]) => (
              <li key={key} className="flex flex-wrap items-center gap-2 font-mono text-xs">
                <Badge variant="secondary">新增</Badge>
                <span className="text-muted-foreground">{renderKey(specs, key)}</span>
                <span className="text-stock-down">{renderValue(specs, key, value)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {removed.length > 0 && (
        <div className="space-y-1">
          <div className="font-medium">移除（{removed.length}）</div>
          <ul className="space-y-1">
            {removed.map(([key, value]) => (
              <li key={key} className="flex flex-wrap items-center gap-2 font-mono text-xs">
                <Badge variant="destructive">移除</Badge>
                <span className="text-muted-foreground">{renderKey(specs, key)}</span>
                <span className="text-destructive line-through">
                  {renderValue(specs, key, value)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
