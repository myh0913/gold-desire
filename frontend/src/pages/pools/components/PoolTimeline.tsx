/**
 * 封板时间线：把上游的 `status` 序列渲染成一排「时间 + 状态」。
 *
 * 上游 `limit_timeline.items[] = [{timestamp(Unix 秒), status}]`，
 * status 含义：1 封涨停 / 2 炸板 / 3 封跌停 / 4 开跌停。
 *
 * 时间一律经 `lib/time.hhmm` 格式化（固定 Asia/Shanghai，避免本地时区泄漏）；
 * 注意该函数按 **毫秒** 解释数字，故此处把 Unix 秒 ×1000。
 */

import type { ComponentProps } from 'react';
import { Badge } from '@/components/ui/badge';
import { hhmm } from '@/lib/time';
import type { PoolTimelinePoint } from '@/types/market';

type BadgeVariant = NonNullable<ComponentProps<typeof Badge>['variant']>;

/** status → 文案与徽标变体。 */
const STATUS_META: Record<number, { label: string; variant: BadgeVariant }> = {
  1: { label: '封涨停', variant: 'up' },
  2: { label: '炸板', variant: 'outline' },
  3: { label: '封跌停', variant: 'down' },
  4: { label: '开跌停', variant: 'secondary' },
};

const UNKNOWN = { label: '未知', variant: 'secondary' } as const;

interface PoolTimelineProps {
  items: PoolTimelinePoint[] | null | undefined;
}

export function PoolTimeline({ items }: PoolTimelineProps) {
  if (!items || items.length === 0) {
    return <span className="text-muted-foreground text-xs">--</span>;
  }

  return (
    <ol className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {items.map((point, index) => {
        const meta = STATUS_META[point.status] ?? UNKNOWN;
        return (
          <li key={`${point.timestamp}-${index}`} className="flex items-center gap-1.5">
            <span className="text-muted-foreground font-mono text-xs">
              {hhmm(point.timestamp * 1000)}
            </span>
            <Badge variant={meta.variant}>{meta.label}</Badge>
          </li>
        );
      })}
    </ol>
  );
}
