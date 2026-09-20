/**
 * 可复用横向时间轴（分时/事件轴）。
 *
 * 原项目在三个页面各手写了一份时间轴，此处统一为一个受控组件：
 * 传入 `start`/`end`（`HH:mm`）与标记点，内部按分钟线性映射为百分比定位。
 */

import { cn } from '@/lib/utils';

export type TimeLineTone = 'up' | 'down' | 'flat' | 'accent';

export interface TimeLineMarker {
  /** `HH:mm` */
  time: string;
  label?: string;
  tone?: TimeLineTone;
}

export interface TimeLineProps {
  /** 起点 `HH:mm` */
  start: string;
  /** 终点 `HH:mm` */
  end: string;
  markers?: readonly TimeLineMarker[];
  /** 刻度数量（默认 5） */
  ticks?: number;
  ariaLabel?: string;
  className?: string;
}

const TONE_CLASS: Record<TimeLineTone, string> = {
  up: 'bg-stock-up',
  down: 'bg-stock-down',
  flat: 'bg-stock-flat',
  accent: 'bg-primary',
};

/** `HH:mm(:ss)` → 当日分钟数；非法输入返回 null。 */
function toMinutes(value: string): number | null {
  const match = /^(\d{1,2}):(\d{2})/.exec(value);
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return null;
  return hours * 60 + minutes;
}

function fromMinutes(total: number): string {
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
}

export function TimeLine({
  start,
  end,
  markers = [],
  ticks = 5,
  ariaLabel,
  className,
}: TimeLineProps) {
  const startMinutes = toMinutes(start);
  const endMinutes = toMinutes(end);
  if (startMinutes === null || endMinutes === null || endMinutes <= startMinutes) {
    return null;
  }

  const span = endMinutes - startMinutes;
  const position = (minutes: number) => ((minutes - startMinutes) / span) * 100;

  const tickList = Array.from({ length: Math.max(2, ticks) }, (_, index) => {
    const minutes = startMinutes + Math.round((span * index) / (Math.max(2, ticks) - 1));
    return { minutes, label: fromMinutes(minutes), pct: position(minutes) };
  });

  const markerList = markers
    .map((marker) => {
      const minutes = toMinutes(marker.time);
      if (minutes === null) return null;
      return { ...marker, minutes, pct: Math.min(100, Math.max(0, position(minutes))) };
    })
    .filter((item): item is TimeLineMarker & { minutes: number; pct: number } => item !== null);

  return (
    <div
      role="img"
      aria-label={ariaLabel ?? `时间轴 ${start} 至 ${end}`}
      className={cn('relative h-16 w-full select-none', className)}
    >
      <div className="bg-border absolute inset-x-0 top-8 h-px" aria-hidden="true" />
      {tickList.map((tick) => (
        <span
          key={tick.minutes}
          className="text-muted-foreground absolute top-10 -translate-x-1/2 text-[10px] tabular-nums"
          style={{ left: `${tick.pct}%` }}
        >
          {tick.label}
        </span>
      ))}
      {markerList.map((marker, index) => (
        <span
          key={`dot-${marker.time}-${index}`}
          title={`${marker.time}${marker.label ? ` ${marker.label}` : ''}`}
          className={cn(
            'ring-background absolute top-8 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2',
            TONE_CLASS[marker.tone ?? 'accent'],
          )}
          style={{ left: `${marker.pct}%` }}
        />
      ))}
      {markerList.map((marker, index) =>
        marker.label ? (
          <span
            key={`label-${marker.time}-${index}`}
            className="text-muted-foreground absolute top-2 -translate-x-1/2 whitespace-nowrap text-[10px]"
            style={{ left: `${marker.pct}%` }}
          >
            {marker.label}
          </span>
        ) : null,
      )}
    </div>
  );
}
