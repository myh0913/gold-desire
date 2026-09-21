/**
 * 昨日建议表现摘要卡（总览页）。
 *
 * 对齐参考实现 quant 的 `YesterdayReviewCard`：读最近一份复盘报告，显示
 * 建议日期、按**路径**（策略分支）的胜率、平均收益与待了结条数，点击进复盘页看明细。
 *
 * 口径（对齐 quant）：
 * - 胜率分母 = **已了结**条数（`status !== 'pending'`），未了结不计，避免拉低胜率；
 * - 平均收益按已了结且有收益值的条目算；
 * - 无报告时**整卡不渲染**（避免总览闪出空块）——库里目前 dragon 策略产不出建议
 *   （分时数据覆盖不足），此时本卡按设计静默隐藏。
 */

import { Link } from 'react-router-dom';
import { TrendingUp } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { useQuery } from '@tanstack/react-query';
import { reportApi } from '@/lib/api';
import { formatPercentPlain } from '@/lib/format';
import type { ReviewAdviceOutcomeOut, ReviewResponse } from '@/types/review';

/** 按路径（策略分支）聚合胜率与平均收益。 */
interface PathAgg {
  settled: number;
  win: number;
  returns: number[];
}

function aggregate(advices: ReviewAdviceOutcomeOut[]): Map<string, PathAgg> {
  const byPath = new Map<string, PathAgg>();
  for (const advice of advices) {
    const key = advice.path_label || advice.path_id || '未命名路径';
    const agg = byPath.get(key) ?? { settled: 0, win: 0, returns: [] };
    // 未了结（pending）不计入胜率分母。
    if (advice.status !== 'pending') {
      agg.settled += 1;
      const value = advice.return_pct;
      if (value != null) {
        agg.returns.push(value);
        if (value > 0) agg.win += 1;
      }
    }
    byPath.set(key, agg);
  }
  return byPath;
}

function toneClass(value: number): string {
  return value >= 0 ? 'text-stock-up' : 'text-stock-down';
}

export function YesterdayReviewCard() {
  const { data } = useQuery<ReviewResponse | null>({
    queryKey: ['review', 'latest'],
    queryFn: async ({ signal }) => {
      const dates = await reportApi.reviewDates(signal);
      const latest = dates.dates?.[0];
      if (!latest) return null;
      return await reportApi.review({ date: latest }, signal);
    },
    staleTime: 60_000,
    refetchInterval: 120_000,
  });

  // 无报告 / 报告内无建议 → 不占位。
  if (!data || data.advices.length === 0) return null;

  const byPath = aggregate(data.advices);
  const allReturns = [...byPath.values()].flatMap((agg) => agg.returns);
  const avg = allReturns.length
    ? allReturns.reduce((sum, value) => sum + value, 0) / allReturns.length
    : null;
  const pending = data.advice_stats.pending;

  return (
    <Link to="/review" className="block" data-testid="yesterday-review-card">
      <Card className="hover:border-primary/40 transition-colors">
        <CardContent className="p-4">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <TrendingUp className="text-stock-up size-4 shrink-0" aria-hidden />
            <span className="text-sm font-medium">昨日建议表现</span>
            <span className="text-muted-foreground text-[11px]">
              {data.trade_date} 的建议 · 按已了结核算 · 点击查看明细
            </span>

            <div className="ml-auto flex flex-wrap items-center gap-3">
              {[...byPath.entries()].map(([path, agg]) => {
                const winRate = agg.settled ? agg.win / agg.settled : null;
                return (
                  <span key={path} className="text-muted-foreground">
                    {path}{' '}
                    <span
                      className={
                        winRate != null
                          ? `font-medium tabular-nums ${toneClass(winRate - 0.5)}`
                          : 'font-medium'
                      }
                    >
                      {winRate != null ? formatPercentPlain(winRate * 100) : '—'}
                    </span>
                    <span className="text-muted-foreground/70"> ({agg.settled})</span>
                  </span>
                );
              })}
              <span className="text-muted-foreground">
                平均收益{' '}
                <span className={avg != null ? `font-medium tabular-nums ${toneClass(avg)}` : ''}>
                  {avg != null ? formatPercentPlain(avg * 100) : '—'}
                </span>
              </span>
              {pending > 0 && (
                <span className="text-muted-foreground">待了结 {pending}</span>
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
