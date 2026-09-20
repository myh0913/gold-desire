/**
 * 因子配置页（Task 15.2）：类别筛选 + 关键词搜索 + 参数表单 + 版本历史 + 有效性。
 *
 * 列表与搜索均为前端过滤（因子数量有限，后端 `/factors` 无筛选参数）。
 */

import { useMemo, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { EmptyState, ErrorState, LoadingState } from '@/components/common/StateViews';
import { cn } from '@/lib/utils';
import type { FactorOut } from '@/types/config';
import { useFactorsQuery } from '../hooks';
import { formatParamValue, parseParamSpecs } from '../lib/paramSchema';
import { FactorDetail } from './FactorDetail';

export interface FactorsPanelProps {
  isAdmin: boolean;
}

/** 生效参数摘要（最多展示 3 项，超出以 `…` 收尾）。 */
function paramsSummary(factor: FactorOut): string {
  const specs = parseParamSpecs(factor.params_schema);
  const parts = specs
    .filter((spec) => factor.active_params[spec.key] !== undefined)
    .slice(0, 3)
    .map((spec) => `${spec.key}=${formatParamValue(spec, factor.active_params[spec.key])}`);
  if (parts.length === 0) return '默认参数';
  return parts.length < specs.length || specs.length > 3
    ? `${parts.join(' · ')} …`
    : parts.join(' · ');
}

export function FactorsPanel({ isAdmin }: FactorsPanelProps) {
  const factors = useFactorsQuery();
  const [category, setCategory] = useState('all');
  const [keyword, setKeyword] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const items = useMemo(() => factors.data?.items ?? [], [factors.data]);

  const categories = useMemo(
    () => [...new Set(items.map((item) => item.category))].sort(),
    [items],
  );

  const filtered = useMemo(() => {
    const needle = keyword.trim().toLowerCase();
    return items.filter((item) => {
      if (category !== 'all' && item.category !== category) return false;
      if (!needle) return true;
      return (
        item.factor_id.toLowerCase().includes(needle) ||
        item.label.toLowerCase().includes(needle) ||
        (item.description ?? '').toLowerCase().includes(needle)
      );
    });
  }, [items, category, keyword]);

  if (factors.isPending) return <LoadingState title="加载因子列表…" />;
  if (factors.error) {
    return <ErrorState error={factors.error} onRetry={() => void factors.refetch()} />;
  }

  const selected = filtered.find((item) => item.factor_id === selectedId) ?? filtered[0];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-44 space-y-1.5">
          <label className="text-xs" htmlFor="factor-category">
            类别
          </label>
          <Select
            id="factor-category"
            value={category}
            onChange={(event) => setCategory(event.target.value)}
          >
            <option value="all">全部（{items.length}）</option>
            {categories.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </Select>
        </div>
        <div className="w-64 space-y-1.5">
          <label className="text-xs" htmlFor="factor-search">
            搜索
          </label>
          <Input
            id="factor-search"
            value={keyword}
            placeholder="因子 id / 名称 / 说明"
            onChange={(event) => setKeyword(event.target.value)}
          />
        </div>
        <Badge variant="secondary">命中 {filtered.length} 个因子</Badge>
      </div>

      {filtered.length === 0 ? (
        <EmptyState title="无匹配因子" description="调整类别或关键词后重试。" />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[16rem_1fr]">
          <Card className="h-fit">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">因子列表</CardTitle>
              <CardDescription>共 {filtered.length} 项</CardDescription>
            </CardHeader>
            <CardContent className="max-h-[32rem] space-y-1 overflow-y-auto">
              {filtered.map((item) => (
                <button
                  key={item.factor_id}
                  type="button"
                  onClick={() => setSelectedId(item.factor_id)}
                  className={cn(
                    'w-full rounded-md border border-transparent px-3 py-2 text-left text-sm transition-colors',
                    item.factor_id === selected.factor_id
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
                    {item.factor_id} · {item.category}
                  </div>
                  <div className="text-muted-foreground truncate text-[11px]">
                    {paramsSummary(item)}
                  </div>
                </button>
              ))}
            </CardContent>
          </Card>

          <FactorDetail
            key={`${selected.factor_id}:${selected.active_version ?? 'default'}`}
            factor={selected}
            isAdmin={isAdmin}
          />
        </div>
      )}
    </div>
  );
}
