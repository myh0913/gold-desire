/**
 * 策略配置页（Task 15.1）：策略卡片列表（含门控矩阵摘要）+ 详情编辑。
 *
 * 可访问性：路由守卫按后端注册表 `quantconfig` key 判定；写操作 admin-only，
 * 非 admin 由 `StrategyDetail` 降级为只读视图。
 */

import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/common/StateViews';
import { cn } from '@/lib/utils';
import type { StrategyOut } from '@/types/config';
import { useStrategiesQuery } from '../hooks';
import { StrategyDetail } from './StrategyDetail';

export interface StrategiesPanelProps {
  /** 是否管理员（决定是否渲染写操作控件） */
  isAdmin: boolean;
}

/** 门控摘要：`N/M 态允许`（矩阵为空表示策略未声明周期门控）。 */
function gateSummary(strategy: StrategyOut): string {
  const entries = Object.values(strategy.gate_matrix ?? {});
  if (entries.length === 0) return '未声明门控';
  const allowed = entries.filter((rule) => rule?.allowed).length;
  return `门控 ${allowed}/${entries.length} 允许`;
}

export function StrategiesPanel({ isAdmin }: StrategiesPanelProps) {
  const strategies = useStrategiesQuery();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  if (strategies.isPending) return <LoadingState title="加载策略列表…" />;
  if (strategies.error) {
    return <ErrorState error={strategies.error} onRetry={() => void strategies.refetch()} />;
  }

  const items = strategies.data?.items ?? [];
  if (items.length === 0) {
    return <EmptyState title="暂无策略" description="后端策略注册表为空。" />;
  }

  const selected = items.find((item) => item.strategy_id === selectedId) ?? items[0];

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[16rem_1fr]">
      <Card className="h-fit">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">策略列表</CardTitle>
          <CardDescription>共 {items.length} 个策略插件</CardDescription>
        </CardHeader>
        <CardContent className="space-y-1">
          {items.map((item) => (
            <button
              key={item.strategy_id}
              type="button"
              onClick={() => setSelectedId(item.strategy_id)}
              className={cn(
                'w-full rounded-md border border-transparent px-3 py-2 text-left text-sm transition-colors',
                item.strategy_id === selected.strategy_id
                  ? 'bg-primary/15 text-primary border-primary/30'
                  : 'hover:bg-accent hover:text-accent-foreground',
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium">{item.label}</span>
                <Badge variant={item.enabled ? 'default' : 'outline'}>
                  {item.enabled ? '启用' : '停用'}
                </Badge>
              </div>
              <div className="text-muted-foreground truncate font-mono text-[11px]">
                {item.strategy_id} · v{item.version}
              </div>
              <div className="text-muted-foreground text-[11px]">{gateSummary(item)}</div>
            </button>
          ))}
        </CardContent>
      </Card>

      <StrategyDetail
        key={`${selected.strategy_id}:${selected.active_version ?? 'default'}`}
        strategy={selected}
        isAdmin={isAdmin}
      />
    </div>
  );
}
