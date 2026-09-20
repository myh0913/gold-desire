/**
 * 走势图 1/3：情绪温度（左轴 0~100）+ 昨日涨停今日溢价（右轴 %）双轴曲线。
 */

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { ChartContainer } from '@/components/common/ChartContainer';
import type { SentimentHistoryRow } from '../lib/historyRows';

export interface TemperaturePremiumChartProps {
  rows: SentimentHistoryRow[];
}

export function TemperaturePremiumChart({ rows }: TemperaturePremiumChartProps) {
  return (
    <ChartContainer title="情绪温度 · 涨停溢价" height={240}>
      <LineChart data={rows} margin={{ top: 8, right: 4, bottom: 0, left: -16 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
        <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={24} />
        <YAxis
          yAxisId="temp"
          domain={[0, 100]}
          tick={{ fontSize: 10 }}
          tickFormatter={(value: number) => `${value}`}
        />
        <YAxis
          yAxisId="premium"
          orientation="right"
          tick={{ fontSize: 10 }}
          tickFormatter={(value: number) => `${value}%`}
        />
        <Tooltip
          contentStyle={{ fontSize: 12 }}
          labelFormatter={(_, items) =>
            (items?.[0]?.payload as SentimentHistoryRow | undefined)?.fullDate ?? ''
          }
          formatter={(value: number, name: string) =>
            name === '温度' ? [value.toFixed(1), name] : [`${value.toFixed(2)}%`, name]
          }
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Line
          yAxisId="temp"
          type="monotone"
          dataKey="temperature"
          name="温度"
          stroke="hsl(var(--stock-up))"
          strokeWidth={2}
          dot={false}
        />
        <Line
          yAxisId="premium"
          type="monotone"
          dataKey="premiumPct"
          name="涨停溢价"
          stroke="#10b981"
          strokeWidth={1.5}
          dot={false}
        />
      </LineChart>
    </ChartContainer>
  );
}
