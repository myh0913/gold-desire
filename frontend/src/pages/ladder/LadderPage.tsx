/**
 * 连板天梯页：Excel 式**列布局**回看每日 ≥2 连板的个股（对齐参考实现 quant）。
 *
 * 布局口径：
 * - 横向往右 = 交易日递增；**每列单独**按当天连板数降序、同板数按首封时间升序排列
 *   （不做跨日期行对齐，避免被"某票某天没上榜"撑出大量空格）；
 * - 单元格 = 股票名称 + 当天连板数，板数越高颜色越暖；
 * - 悬停高亮同一只票在**所有列**的单元格；单击固定高亮，再点取消；
 * - 数据变化后自动滚动到最右（最新交易日）。
 *
 * 数据：`GET /api/ladder/matrix` 一次返回整个区间的聚合（列=日期、行=个股），
 * 全部读库（后端采集侧交易时段每 10 分钟入库）；实时：订阅 WS `pool` 频道。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { StaleNotice } from '@/components/common/StaleNotice';
import { ErrorState, LoadingState } from '@/components/common/StateViews';
import { useChannelRefresh } from '@/hooks/useChannelRefresh';
import { marketApi } from '@/lib/api';
import { daysAgoSh, todaySh } from '@/lib/time';
import { cn } from '@/lib/utils';

/** 连板数 → 文字颜色（板数越高越暖）。 */
function boardToneClass(boards: number): string {
  if (boards >= 5) return 'text-amber-300';
  if (boards === 4) return 'text-violet-300';
  if (boards === 3) return 'text-sky-300';
  return 'text-foreground';
}

interface DayCell {
  code: string;
  name: string;
  boards: number;
  firstSeal: string;
}

/** 日期预设（天）。 */
const PRESETS: { label: string; spanDays: number }[] = [
  { label: '近60天', spanDays: 59 },
  { label: '近120天', spanDays: 119 },
  { label: '近一年', spanDays: 365 },
];

export default function LadderPage() {
  useChannelRefresh(['pool'], ['market', 'ladder']);

  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [minDays, setMinDays] = useState(2);
  /** 已提交查询的区间；null 表示「近 30 个交易日」默认视图（跟随 WS 刷新）。 */
  const [range, setRange] = useState<{ start: string; end: string } | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);

  const matrixQuery = useQuery({
    queryKey: ['market', 'ladder', 'matrix', range?.start ?? '', range?.end ?? '', minDays],
    queryFn: ({ signal }) =>
      marketApi.ladderMatrix(
        {
          min_continue_days: minDays,
          limit_days: 30,
          ...(range ? { start: range.start, end: range.end } : {}),
        },
        signal,
      ),
  });

  const days = matrixQuery.data?.days ?? [];
  const rows = matrixQuery.data?.rows ?? [];

  /** 每个交易日一列：列内按连板数降序 → 首封时间升序。 */
  const perDay = useMemo(() => {
    const map: Record<string, DayCell[]> = {};
    for (const day of days) map[day] = [];
    for (const row of rows) {
      for (const [day, cell] of Object.entries(row.cells)) {
        if (!map[day]) continue;
        map[day].push({
          code: row.code,
          name: row.name,
          boards: cell.boards,
          firstSeal: cell.first_seal_time ?? '99:99',
        });
      }
    }
    for (const day of Object.keys(map)) {
      map[day].sort((a, b) => b.boards - a.boards || a.firstSeal.localeCompare(b.firstSeal));
    }
    return map;
  }, [days, rows]);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    // 数据/区间变化后滚到最右（最新交易日）
    if (scrollRef.current && days.length > 0) {
      scrollRef.current.scrollLeft = scrollRef.current.scrollWidth;
    }
  }, [days, range]);

  /** 应用预设区间（按自然日跨度，后端按交易日取）。 */
  const applyPreset = useCallback((spanDays: number) => {
    const next = { start: daysAgoSh(spanDays), end: todaySh() };
    setStart(next.start);
    setEnd(next.end);
    setRange(next);
  }, []);

  /** 提交自定义区间（自动纠正起止顺序）。 */
  const submitRange = useCallback(() => {
    if (!start || !end) return;
    const [from, to] = start <= end ? [start, end] : [end, start];
    setStart(from);
    setEnd(to);
    setRange({ start: from, end: to });
  }, [start, end]);

  const backToDefault = useCallback(() => {
    setStart('');
    setEnd('');
    setRange(null);
  }, []);

  /** 导出当前视图为 CSV（列=日期，列内顺序与页面一致）。 */
  const exportCsv = useCallback(() => {
    if (days.length === 0) return;
    const maxLen = Math.max(...days.map((day) => (perDay[day] ?? []).length), 1);
    const lines: string[] = [days.join(',')];
    for (let i = 0; i < maxLen; i += 1) {
      lines.push(
        days
          .map((day) => {
            const cell = (perDay[day] ?? [])[i];
            return cell ? `"${cell.name} ${cell.boards}板"` : '';
          })
          .join(','),
      );
    }
    const blob = new Blob([`\ufeff${lines.join('\n')}`], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `连板天梯_${days[0]}_${days[days.length - 1]}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }, [days, perDay]);

  if (matrixQuery.isLoading) return <LoadingState title="加载连板天梯…" />;
  if (matrixQuery.error) {
    return <ErrorState error={matrixQuery.error} onRetry={() => void matrixQuery.refetch()} />;
  }

  const presetActive = (spanDays: number) =>
    range !== null && range.start === daysAgoSh(spanDays) && range.end === todaySh();

  return (
    <div className="flex min-h-0 flex-1 flex-col space-y-3 p-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-xl font-semibold">连板天梯</h1>
        <span className="text-muted-foreground text-xs">
          {days.length} 个交易日 · {rows.length} 只 ≥{minDays} 连板 · 列内按当天连板数降序，
          同板数按首封时间
        </span>
        <span className="text-muted-foreground ml-auto text-xs">
          悬停高亮同一只票 · 点击固定，再点取消
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Button
          variant={range === null ? 'default' : 'outline'}
          size="sm"
          onClick={backToDefault}
        >
          近30交易日
        </Button>
        {PRESETS.map((preset) => (
          <Button
            key={preset.label}
            variant={presetActive(preset.spanDays) ? 'default' : 'outline'}
            size="sm"
            onClick={() => applyPreset(preset.spanDays)}
          >
            {preset.label}
          </Button>
        ))}
        <span className="text-muted-foreground mx-1">|</span>
        <Label htmlFor="ladder-start" className="text-xs">
          起始日
        </Label>
        <Input
          id="ladder-start"
          type="date"
          value={start}
          onChange={(event) => setStart(event.target.value)}
          className="w-36"
        />
        <Label htmlFor="ladder-end" className="text-xs">
          结束日
        </Label>
        <Input
          id="ladder-end"
          type="date"
          value={end}
          onChange={(event) => setEnd(event.target.value)}
          className="w-36"
        />
        <Label htmlFor="ladder-min" className="text-xs">
          最低连板
        </Label>
        <Input
          id="ladder-min"
          type="number"
          min={2}
          max={20}
          value={minDays}
          onChange={(event) => setMinDays(Math.max(2, Number(event.target.value) || 2))}
          className="w-20"
        />
        <Button size="sm" onClick={submitRange} disabled={!start || !end}>
          查询
        </Button>
        <Button variant="outline" size="sm" onClick={exportCsv} disabled={days.length === 0}>
          导出 CSV
        </Button>
      </div>

      <StaleNotice
        stale={matrixQuery.data?.stale}
        dataDate={matrixQuery.data?.data_date}
        label="连板天梯"
      />

      {days.length === 0 ? (
        <p className="text-muted-foreground text-sm">区间无天梯数据。</p>
      ) : (
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto rounded-md border">
          <div className="flex items-start gap-0">
            {days.map((day) => (
              <div key={day} className="min-w-[7.5rem] flex-1 border-r last:border-r-0">
                <div className="bg-muted/40 text-muted-foreground sticky top-0 border-b px-2 py-1.5 text-center font-mono text-xs">
                  {day.slice(5)}
                </div>
                <ol className="space-y-0.5 p-1">
                  {(perDay[day] ?? []).map((cell) => {
                    const highlighted = pinned === cell.code;
                    return (
                      <li key={`${day}-${cell.code}`}>
                        <button
                          type="button"
                          onClick={() => setPinned(highlighted ? null : cell.code)}
                          title={`${cell.name}（${cell.code}） ${cell.boards} 板 · 首封 ${
                            cell.firstSeal === '99:99' ? '--' : cell.firstSeal
                          }`}
                          className={cn(
                            'w-full rounded px-1.5 py-1 text-left text-xs transition-colors hover:bg-accent',
                            highlighted && 'bg-accent ring-1 ring-ring',
                          )}
                        >
                          <span className="block truncate">{cell.name}</span>
                          <span
                            className={cn('font-mono text-[10px]', boardToneClass(cell.boards))}
                          >
                            {cell.boards} 板
                          </span>
                        </button>
                      </li>
                    );
                  })}
                  {(perDay[day] ?? []).length === 0 && (
                    <li className="text-muted-foreground px-1.5 py-1 text-xs">--</li>
                  )}
                </ol>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
