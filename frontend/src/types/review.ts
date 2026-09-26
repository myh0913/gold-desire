/**
 * 复盘域契约：某交易日市场情绪 / 池型统计 / 天梯头部 / 建议回溯。
 *
 * 逐字段镜像后端 `app/schemas/review.py`。比率字段为**小数口径**。
 */

import type { SentimentOut } from './market';

/** 涨停池头部个股。 */
export interface ReviewPoolTopOut {
  code: string;
  name: string;
  continue_days: number;
  limit_up_time: string | null;
  seal_amount_yuan: number | null;
  turnover_rate: number | null;
}

/** 建议回溯状态：pending（未到可评估）/ stopped（触发止损）/ closed（收盘了结）。 */
export type AdviceOutcomeStatus = 'pending' | 'stopped' | 'closed';

/** 单条建议的回溯结果。 */
export interface ReviewAdviceOutcomeOut {
  code: string;
  name: string | null;
  /** 产出该建议的策略 id（跨日分策略聚合的分组键）。 */
  strategy_id: string | null;
  /** 该建议当日是否已被标记「已买入」（建议页标记串联）。 */
  bought: boolean;
  path_id: string;
  path_label: string | null;
  buy_day: string | null;
  buy_price: number | null;
  position: number | null;
  stop_loss_price: number | null;
  sell_timing: string | null;
  bonus_score: number | null;
  status: AdviceOutcomeStatus;
  return_pct: number | null;
  sell_date: string | null;
  sell_price: number | null;
  ran_at: string | null;
}

/** 建议回溯汇总。 */
export interface ReviewAdviceStatsOut {
  total: number;
  settled: number;
  pending: number;
  win_count: number;
  win_rate: number | null;
  avg_return_pct: number | null;
}

/** 单策略跨日战绩聚合（口径与 :interface:`ReviewAdviceStatsOut` 一致）。 */
export interface ReviewStrategyStatsOut {
  strategy_id: string;
  total: number;
  settled: number;
  pending: number;
  win_count: number;
  win_rate: number | null;
  avg_return_pct: number | null;
}

/** 跨日分策略战绩聚合视图（`days=0` 表示全部历史）。 */
export interface ReviewStrategyStatsResponse {
  days: number;
  start_date: string | null;
  end_date: string | null;
  items: ReviewStrategyStatsOut[];
}

/** 某交易日复盘聚合视图。 */
export interface ReviewResponse {
  trade_date: string | null;
  sentiment: SentimentOut | null;
  prev_trade_date: string | null;
  prev_temperature: number | null;
  temperature_delta: number | null;
  pool_counts: Record<string, number>;
  top_ladder: ReviewPoolTopOut[];
  advices: ReviewAdviceOutcomeOut[];
  advice_stats: ReviewAdviceStatsOut;
  meta: Record<string, unknown>;
}
