/**
 * 情绪统计卡：涨停 / 跌停 / 炸板（含炸板率）/ 上涨 / 下跌。
 */

import { Card, CardContent } from '@/components/ui/card';
import { formatNumber, formatPercentPlain } from '@/lib/format';
import { cn } from '@/lib/utils';
import type { SentimentOut } from '@/types/market';

export interface StatCardsProps {
  sentiment: SentimentOut;
}

interface StatItem {
  label: string;
  value: number;
  tone: 'up' | 'down' | 'warn' | 'plain';
  hint?: string;
}

const TONE_CLASS: Record<StatItem['tone'], string> = {
  up: 'text-stock-up',
  down: 'text-stock-down',
  warn: 'text-amber-400',
  plain: 'text-foreground',
};

export function StatCards({ sentiment }: StatCardsProps) {
  const stats: StatItem[] = [
    { label: '涨停', value: sentiment.limit_up_count, tone: 'up' },
    { label: '跌停', value: sentiment.limit_down_count, tone: 'down' },
    {
      label: '炸板',
      value: sentiment.broken_board_count,
      tone: 'warn',
      hint: `炸板率 ${formatPercentPlain(sentiment.broken_rate * 100, 0)}`,
    },
    { label: '上涨', value: sentiment.up_count, tone: 'up' },
    { label: '下跌', value: sentiment.down_count, tone: 'down' },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {stats.map((item) => (
        <Card key={item.label}>
          <CardContent className="space-y-1 p-4">
            <div className="text-muted-foreground text-xs">{item.label}</div>
            <div className={cn('text-2xl font-semibold tabular-nums', TONE_CLASS[item.tone])}>
              {formatNumber(item.value)}
            </div>
            <div className="text-muted-foreground text-[11px]">{item.hint ?? '\u00a0'}</div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
