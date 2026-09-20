/**
 * 情绪温度计：0~100 温度半圆仪表 + 周期阶段标签 + 连板高度 / 涨停溢价脚注。
 *
 * 阶段名由后端下发（口径见 `app/strategies/protocol.py`：冰点 / 冰点转折 / 修复 /
 * 加速/高潮 / 分歧 / 退潮），前端不重复实现分档逻辑。
 */

import { cn } from '@/lib/utils';
import { formatPct } from '@/lib/format';

export interface SentimentGaugeProps {
  /** 情绪温度（0~100） */
  temperature: number;
  /** 情绪周期阶段（后端 `stage`）；后端未派生时为 null */
  stage?: string | null;
  /** 连板高度（最高连板天数，板）；源缺失时为 null */
  maxContinueDays?: number | null;
  /** 昨日涨停今日平均溢价（小数口径） */
  premiumRate?: number;
  className?: string;
}

const RADIUS = 80;
const ARC_LENGTH = Math.PI * RADIUS;
const ARC_PATH = `M 30 110 A ${RADIUS} ${RADIUS} 0 0 1 190 110`;

/** 温度 → 描边色（冷/中性/热）。 */
function toneClass(temperature: number): string {
  if (temperature < 30) return 'stroke-sky-400';
  if (temperature < 60) return 'stroke-amber-400';
  return 'stroke-rose-500';
}

export function SentimentGauge({
  temperature,
  stage,
  maxContinueDays,
  premiumRate,
  className,
}: SentimentGaugeProps) {
  const clamped = Math.max(0, Math.min(100, Number.isFinite(temperature) ? temperature : 0));
  const filled = (clamped / 100) * ARC_LENGTH;
  const hasPremium = typeof premiumRate === 'number' && Number.isFinite(premiumRate);
  const premiumTone = !hasPremium
    ? 'text-muted-foreground'
    : premiumRate! > 0
      ? 'text-stock-up'
      : premiumRate! < 0
        ? 'text-stock-down'
        : 'text-muted-foreground';

  return (
    <div className={cn('flex flex-col items-center', className)}>
      <svg
        viewBox="0 0 220 140"
        className="w-full max-w-[16rem]"
        role="img"
        aria-label={`情绪温度 ${clamped.toFixed(1)} 分，阶段 ${stage || '--'}`}
      >
        <path
          d={ARC_PATH}
          fill="none"
          strokeWidth={14}
          strokeLinecap="round"
          className="stroke-muted"
        />
        <path
          d={ARC_PATH}
          fill="none"
          strokeWidth={14}
          strokeLinecap="round"
          className={toneClass(clamped)}
          strokeDasharray={`${filled} ${ARC_LENGTH}`}
        />
        <text
          x="110"
          y="96"
          textAnchor="middle"
          className="fill-foreground text-[34px] font-semibold"
        >
          {clamped.toFixed(1)}
        </text>
        <text x="110" y="118" textAnchor="middle" className="fill-muted-foreground text-[12px]">
          情绪温度 / 100
        </text>
      </svg>
      <div className="mt-2 flex items-center gap-2">
        <span className="text-muted-foreground text-xs">周期阶段</span>
        <span className="rounded-md border px-2 py-0.5 text-sm font-medium">{stage || '--'}</span>
      </div>
      <div
        data-testid="gauge-footnote"
        className="text-muted-foreground mt-2 flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-xs"
      >
        <span>
          连板高度{' '}
          <span className="text-foreground font-mono font-medium">
            {typeof maxContinueDays === 'number' ? `${maxContinueDays} 板` : '--'}
          </span>
        </span>
        <span>
          涨停溢价{' '}
          <span className={cn('font-mono font-medium', premiumTone)}>
            {hasPremium ? formatPct(premiumRate! * 100) : '--'}
          </span>
        </span>
      </div>
    </div>
  );
}
