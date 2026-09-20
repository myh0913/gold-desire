/**
 * 量化配置页（Task 15.1 / 15.2 / 15.3）：策略 / 因子 / 数据源三个标签。
 *
 * 页面可访问性由路由守卫按后端注册表 `quantconfig` key 判定（admin-only 默认）；
 * 写操作另需 admin，非 admin 由各面板降级为只读视图（服务端仍是权威）。
 * 标签面板按需挂载（未激活的 TabsContent 不渲染），切换标签时各自拉取数据。
 */

import { useState } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { usePageAccess } from '@/hooks/useAuth';
import { DatasourcesPanel } from './datasources/DatasourcesPanel';
import { FactorsPanel } from './factors/FactorsPanel';
import { StrategiesPanel } from './strategies/StrategiesPanel';

const TABS = [
  { value: 'strategies', label: '策略' },
  { value: 'factors', label: '因子' },
  { value: 'datasources', label: '数据源' },
] as const;

export default function QuantConfigPage() {
  const { isAdmin } = usePageAccess();
  const [tab, setTab] = useState<string>('strategies');

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">量化配置</h1>
        <p className="text-muted-foreground text-sm">
          策略与因子参数按 schema 驱动渲染，配置入库并版本化；改动下一轮采集热生效，可一键回滚。
          {!isAdmin && ' 当前角色为只读，写操作仅管理员可用。'}
        </p>
      </header>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          {TABS.map((item) => (
            <TabsTrigger key={item.value} value={item.value}>
              {item.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="strategies">
          <StrategiesPanel isAdmin={isAdmin} />
        </TabsContent>
        <TabsContent value="factors">
          <FactorsPanel isAdmin={isAdmin} />
        </TabsContent>
        <TabsContent value="datasources">
          <DatasourcesPanel isAdmin={isAdmin} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
