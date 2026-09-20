/**
 * 通用占位页：路由必须挂上，否则命中通配会被当成「无访问权限」。
 *
 * Phase 1 阶段：让侧栏所有项都可点、可访问，看得见后端的实时数据是否到位。
 * Phase 2：各页实装后替换为真正的页面组件。
 */
import { Card, CardContent } from '@/components/ui/card';
import { EmptyState } from '@/components/common/StateViews';

export interface PlaceholderPageProps {
  /** 显示在标题里 */
  title: string;
  /** Phase 2 实现前的说明 */
  description?: string;
}

export function PlaceholderPage({ title, description }: PlaceholderPageProps) {
  return (
    <div className="space-y-4 p-4">
      <h1 className="text-xl font-semibold">{title}</h1>
      <Card>
        <CardContent className="p-6">
          <EmptyState
            title="该页面属于 Phase 2（不在本期交付）"
            description={description ?? '本期只交付登录/注册、总览、量化配置、设置四页 + Agent 抽屉。'}
          />
        </CardContent>
      </Card>
    </div>
  );
}

export default PlaceholderPage;