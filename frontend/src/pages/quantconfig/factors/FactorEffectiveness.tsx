/**
 * 因子有效性：档位 × A/B/C 分段分组表 + 单调性可视化。
 *
 * spec 明令**不得只报聚合**，故每档位除全量列外，另展开 A / B / C 三段
 * 各自的样本数、期望收益与胜率。A/B/C 切点为统计时点的中位数快照，会随窗口漂移。
 * 收益着色遵循 A 股红涨绿跌；单调性以条形长度示意，**全量单调 ≠ 分段单调**。
 */

import { Badge } from '@/components/ui/badge';
import { DataTable, type DataTableColumn } from '@/components/common/DataTable';
import { formatNumber, formatPct, priceToneClass } from '@/lib/format';
import { fmtDate } from '@/lib/time';
import { cn } from '@/lib/utils';
import type { BucketStatsOut, FactorEffectivenessResponse } from '@/types/config';

export interface FactorEffectivenessProps {
  data: FactorEffectivenessResponse;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
}

/** 分段键（A/B/C）取各档位并集并按名升序，保证列稳定。 */
function segmentKeys(buckets: readonly BucketStatsOut[]): string[] {
  const keys = new Set<string>();
  for (const bucket of buckets) {
    for (const key of Object.keys(bucket.segments ?? {})) keys.add(key);
  }
  return [...keys].sort();
}

/** 期望收益着色文本 + 比例条（宽度 ∝ |mean| / 全表最大 |mean|）。 */
function MeanReturnCell({
  value,
  maxAbs,
}: {
  value: number;
  maxAbs: number;
}) {
  const width = maxAbs > 0 ? Math.max(4, Math.round((Math.abs(value) / maxAbs) * 100)) : 0;
  return (
    <span className="font-mono text-xs tabular-nums">
      <span className={priceToneClass(value)}>{formatPct(value * 100)}</span>
      <span className="bg-muted mt-1 block h-1 w-full max-w-24 overflow-hidden rounded-full">
        <span
          className={cn(
            'block h-full rounded-full',
            value > 0 ? 'bg-stock-up' : value < 0 ? 'bg-stock-down' : 'bg-stock-flat',
          )}
          style={{ width: `${width}%` }}
        />
      </span>
    </span>
  );
}

function SegmentCell({
  stats,
  maxAbs,
}: {
  stats?: { n: number; mean_return: number; win_rate: number };
  maxAbs: number;
}) {
  if (!stats) return <span className="text-muted-foreground">--</span>;
  return (
    <span className="font-mono text-xs tabular-nums">
      <span className="text-muted-foreground">n={formatNumber(stats.n)} · </span>
      <span className={priceToneClass(stats.mean_return)}>{formatPct(stats.mean_return * 100)}</span>
      <span className="text-muted-foreground"> · {formatPct(stats.win_rate * 100, 1)}</span>
      <span className="bg-muted mt-1 block h-1 w-full max-w-16 overflow-hidden rounded-full">
        <span
          className={cn(
            'block h-full rounded-full',
            stats.mean_return > 0
              ? 'bg-stock-up'
              : stats.mean_return < 0
                ? 'bg-stock-down'
                : 'bg-stock-flat',
          )}
          style={{
            width: `${maxAbs > 0 ? Math.max(4, Math.round((Math.abs(stats.mean_return) / maxAbs) * 100)) : 0}%`,
          }}
        />
      </span>
    </span>
  );
}

export function FactorEffectiveness({
  data,
  loading = false,
  error,
  onRetry,
}: FactorEffectivenessProps) {
  const keys = segmentKeys(data.buckets);

  const maxAbs = Math.max(
    0,
    ...data.buckets.flatMap((bucket) => [
      Math.abs(bucket.mean_return),
      ...Object.values(bucket.segments ?? {}).map((seg) => Math.abs(seg.mean_return)),
    ]),
  );

  const columns: DataTableColumn<BucketStatsOut>[] = [
    { key: 'bucket', header: '档位', render: (row) => <span className="font-medium">{row.bucket}</span> },
    { key: 'n', header: '样本 n', align: 'right', render: (row) => formatNumber(row.n) },
    {
      key: 'mean_return',
      header: '期望收益（全量）',
      align: 'right',
      render: (row) => <MeanReturnCell value={row.mean_return} maxAbs={maxAbs} />,
    },
    {
      key: 'win_rate',
      header: '胜率（全量）',
      align: 'right',
      render: (row) => <span className="font-mono tabular-nums">{formatPct(row.win_rate * 100, 1)}</span>,
    },
    ...keys.map<DataTableColumn<BucketStatsOut>>((key) => ({
      key: `segment-${key}`,
      header: `${key} 段`,
      render: (row) => <SegmentCell stats={row.segments?.[key]} maxAbs={maxAbs} />,
    })),
  ];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge variant="secondary">样本 {formatNumber(data.sample_count)}</Badge>
        <Badge variant="outline">参数版本 {data.params_version}</Badge>
        {data.cuts && (
          <span className="text-muted-foreground">
            A/B/C 切点快照：{fmtDate(data.cuts[0])} / {fmtDate(data.cuts[1])}
            （切点取自统计窗口的中位数位置，窗口变化后会漂移）
          </span>
        )}
      </div>

      <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-400">
        注意：全量单调 ≠ 分段单调。加分项分层仅在全量口径下单调，A/B/C 分段并不单调
        （如 B 段 3~6 分档降至 +0.51%，0 分档 C 段为 -2.18%）。调阈值前请逐段核对上表。
      </p>

      <DataTable
        columns={columns}
        rows={data.buckets}
        rowKey={(row) => row.bucket}
        loading={loading}
        error={error}
        onRetry={onRetry}
        emptyTitle="暂无有效性统计"
        emptyDescription="窗口内无满足条件的样本。"
      />
    </div>
  );
}
