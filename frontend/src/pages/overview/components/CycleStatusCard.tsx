/**
 * 情绪周期状态卡（总览页顶部）。
 *
 * 对齐参考实现 quant 的 `CycleStatusCard`：当前周期六态 + 判定依据 + 指标 + 仓位因子。
 * 数据是后端**派生**的（由情绪指标 + 涨停池按用户 2026-09-07 确认的阈值算出并落库），
 * 缺数据时整卡不渲染（避免总览闪出空块）。
 */

import { Thermometer } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { formatPercentPlain } from '@/lib/format';
import type { CycleOut } from '@/types/market';

/** 六态配色（红热绿冷，与参考实现一致）。 */
const STATE_STYLES: Record<string, string> = {
  冰点: 'text-sky-300 bg-sky-500/10 border-sky-500/30',
  冰点转折: 'text-violet-300 bg-violet-500/10 border-violet-500/30',
  修复: 'text-amber-300 bg-amber-500/10 border-amber-500/30',
  加速: 'text-rose-300 bg-rose-500/10 border-rose-500/30',
  '加速/高潮': 'text-rose-300 bg-rose-500/10 border-rose-500/30',
  分歧: 'text-orange-300 bg-orange-500/10 border-orange-500/30',
  退潮: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/30',
};

/** 仓位因子释义（对齐参考实现的门控档位）。 */
function factorLabel(factor: number): string {
  if (factor <= 0) return '停开新仓';
  if (factor < 1) return `仓位 ×${factor}`;
  return '正常仓位';
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function CycleStatusCard({ cycle }: { cycle: CycleOut | null }) {
  if (!cycle) return null;

  const style = STATE_STYLES[cycle.state] ?? 'text-muted-foreground bg-muted border-border';
  const factor = cycle.position_factor;
  // 后端字段缺失时（老数据 / 精简响应）不应整卡崩掉，一律按空值渲染。
  const ind = cycle.indicators ?? {};
  const reasons = cycle.reasons ?? [];

  const temperature = num(ind.temperature);
  const limitUp = num(ind.limit_up_count);
  const limitDown = num(ind.limit_down_count);
  const breakRatio = num(ind.break_ratio);
  const promotion = num(ind.promotion_rate);
  const height = num(ind.board_height);
  const leader = typeof ind.leader_name === 'string' ? ind.leader_name : '';
  const leaderDown = ind.leader_limit_down === true;

  return (
    <Card
      className={cycle.overheated ? 'border-rose-500/40' : undefined}
      data-testid="cycle-status-card"
    >
      <CardContent className="space-y-2 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Thermometer className="text-muted-foreground size-4 shrink-0" aria-hidden />
          <span className="text-sm font-medium">情绪周期</span>
          <span className={`rounded border px-2 py-0.5 text-xs ${style}`}>
            {cycle.state}
            {cycle.overheated && ' · 过热减半'}
          </span>
          {factor != null && (
            <span
              className={`rounded border px-2 py-0.5 text-xs ${
                factor <= 0
                  ? 'text-rose-300 bg-rose-500/10 border-rose-500/30'
                  : 'text-muted-foreground bg-muted border-border'
              }`}
            >
              仓位因子 {factor} · {factorLabel(factor)}
            </span>
          )}
          {cycle.relaxed_needs_confirm && (
            <span className="text-muted-foreground bg-muted rounded border border-border px-2 py-0.5 text-xs">
              次日复认
            </span>
          )}
          {cycle.data_degraded && (
            <span className="text-amber-300 bg-amber-500/10 border-amber-500/30 rounded border px-2 py-0.5 text-xs">
              数据降级
            </span>
          )}
          <span className="text-muted-foreground ml-auto text-xs tabular-nums">
            {cycle.trade_date}
          </span>
        </div>

        {reasons.length > 0 && (
          <p className="text-muted-foreground text-xs">{reasons.join('；')}</p>
        )}

        <div className="text-muted-foreground flex flex-wrap gap-3 text-xs">
          {temperature != null && (
            <span>
              温度 <span className="text-foreground font-mono tabular-nums">{temperature.toFixed(1)}</span>
            </span>
          )}
          {limitUp != null && (
            <span>
              涨停家数 <span className="text-stock-up font-mono tabular-nums">{limitUp}</span>
            </span>
          )}
          {limitDown != null && (
            <span>
              跌停家数 <span className="text-stock-down font-mono tabular-nums">{limitDown}</span>
            </span>
          )}
          {breakRatio != null && (
            <span>
              炸板率 <span className="text-foreground font-mono tabular-nums">{formatPercentPlain(breakRatio * 100)}</span>
            </span>
          )}
          {promotion != null && (
            <span>
              晋级率 <span className="text-foreground font-mono tabular-nums">{formatPercentPlain(promotion * 100)}</span>
            </span>
          )}
          {height != null && (
            <span>
              高度 <span className="text-foreground font-mono tabular-nums">{height}板</span>
            </span>
          )}
          {leader && (
            <span>
              龙头{' '}
              <span className={leaderDown ? 'text-stock-down' : 'text-foreground'}>
                {leader}
                {leaderDown && '（跌停）'}
              </span>
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
