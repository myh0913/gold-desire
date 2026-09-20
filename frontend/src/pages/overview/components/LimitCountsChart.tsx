/**
 * 走势图 2/3：涨停 / 跌停 / 炸板 家数三曲线（A股口径：红涨、绿跌、amber 炸板）。
 */

import { CartesianGrid, Legend, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts';
import { ChartContainer } from '@/components/common/ChartContainer';
import type { SentimentHistoryRow } from '../lib/historyRows';

export interface LimitCountsChartProps {
  rows: SentimentHistoryRow[];
}

export function LimitCountsChart({ rows }: LimitCountsChartProps) {
  return (
    <ChartContainer title="涨跌停 · 炸板" height={240}>
      <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: -16 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
        <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={24} />
        <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
        <Tooltip
          contentStyle={{ fontSize: 12 }}
          labelFormatter={(_, items) =>
            (items?.[0]?.payload as SentimentHistoryRow | undefined)?.fullDate ?? ''
          }
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Line
          type="monotone"
          dataKey="limitUp"
          name="涨停"
          stroke="hsl(var(--stock-up))"
          strokeWidth={2}
          dot={false}
        />
        <Line
          type="monotone"
          dataKey="limitDown"
          name="跌停"
          stroke="hsl(var(--stock-down))"
          strokeWidth={2}
          dot={false}
        />
        <Line
          type="monotone"
          dataKey="broken"
          name="炸板"
          stroke="#f59e0b"
          strokeWidth={1.5}
          dot={false}
        />
      </LineChart>
    </ChartContainer>
  );
}
