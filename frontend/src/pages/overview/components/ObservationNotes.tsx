/**
 * 观察要点：把 `buildObservations` 的本地点评按语义着色渲染。
 */

import { cn } from '@/lib/utils';
import type { SentimentOut } from '@/types/market';
import { buildObservations, type ObservationTone } from '../lib/commentary';

const TONE_CLASS: Record<ObservationTone, string> = {
  plain: 'text-muted-foreground',
  warn: 'text-amber-400',
  up: 'text-stock-up',
  down: 'text-stock-down',
};

export interface ObservationNotesProps {
  sentiment: SentimentOut;
}

export function ObservationNotes({ sentiment }: ObservationNotesProps) {
  const segments = buildObservations(sentiment);

  return (
    <div className="rounded-lg border p-4">
      <div className="mb-2 text-sm font-medium">观察要点</div>
      <p className="text-sm leading-relaxed">
        {segments.map((segment, index) => (
          <span key={`${segment.tone}-${index}`} className={cn('mr-1', TONE_CLASS[segment.tone])}>
            {segment.text}
          </span>
        ))}
      </p>
      <p className="text-muted-foreground mt-2 text-[11px]">
        由本页根据温度 / 涨停数 / 炸板率 / 溢价本地计算，非投资建议。
      </p>
    </div>
  );
}
