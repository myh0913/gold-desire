/**
 * 走势图 3/3：上涨 / 下跌 家数两曲线（市场广度）。
 */

import { CartesianGrid, Legend, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts';
import { ChartContainer } from '@/components/common/ChartContainer';
import type { SentimentHistoryRow } from '../lib/historyRows';

export interface BreadthChartProps {
  rows: SentimentHistoryRow[];
}

export function BreadthChart({ rows }: BreadthChartProps) {
  return (
    <ChartContainer title="上涨 · 下跌家数" height={240}>
      <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
        <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={24} />
        <YAxis tick={{ fontSize: 10 }} tickFormatter={(value: number) => value.toLocaleString()} />
        <Tooltip
          contentStyle={{ fontSize: 12 }}
          labelFormatter={(_, items) =>
            (items?.[0]?.payload as SentimentHistoryRow | undefined)?.fullDate ?? ''
          }
          formatter={(value: number, name: string) => [value.toLocaleString(), name]}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Line
          type="monotone"
          dataKey="up"
          name="上涨"
          stroke="hsl(var(--stock-up))"
          strokeWidth={2}
          dot={false}
        />
        <Line
          type="monotone"
          dataKey="down"
          name="下跌"
          stroke="hsl(var(--stock-down))"
          strokeWidth={2}
          dot={false}
        />
      </LineChart>
    </ChartContainer>
  );
}
