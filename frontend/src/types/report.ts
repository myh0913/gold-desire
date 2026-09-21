/**
 * 报告域契约：策略建议（advice_reports）与回测任务。
 *
 * 逐字段镜像后端 `app/schemas/report.py`；`payload` 为策略产出的结构化建议
 * （龙回头两路：路次 / 硬门槛 / 加分项 / 仓位 / 止损 / 卖出时点 / 字段快照）。
 */

import type { MarketMeta } from './market';

/** 建议报告行（`payload` 即结构化建议）。 */
export interface AdviceReportOut {
  trade_date: string;
  kind: string;
  strategy_id: string;
  strategy_version: number | null;
  payload: Record<string, unknown>;
  ran_at: string;
  created_at: string | null;
}

/** 某交易日建议报告列表。 */
export interface AdviceResponse extends MarketMeta {
  trade_date: string | null;
  kind: string | null;
  strategy_id: string | null;
  items: AdviceReportOut[];
}

/** 某交易日最近一次运行的建议报告。 */
export interface AdviceLatestResponse extends MarketMeta {
  trade_date: string | null;
  item: AdviceReportOut | null;
}

/** 盘后建池候选行（`dragon_pool`，Phase.POOL 识别、次日开盘判定参考）。 */
export interface DragonPoolItemOut {
  trade_date: string;
  strategy_id: string;
  code: string;
  name: string | null;
  d_date: string;
  boards: number;
  d_amp_pct: number | null;
  shape_label: string | null;
  ran_at: string;
}

/** 某交易日盘后建池候选列表。 */
export interface DragonPoolResponse extends MarketMeta {
  trade_date: string | null;
  strategy_id: string | null;
  items: DragonPoolItemOut[];
}

/** 回测任务（`report` 含 A/B/C 三段汇总，未完成为 null）。 */
export interface BacktestRunOut {
  run_id: string;
  status: string;
  start_date: string;
  end_date: string;
  strategies: string[];
  params: Record<string, unknown>;
  report: Record<string, unknown> | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_by: string | null;
}

/** 最近回测任务列表。 */
export interface BacktestRunsResponse {
  limit: number;
  items: BacktestRunOut[];
}

/** 触发回测请求体。 */
export interface BacktestRunRequest {
  start: string;
  end: string;
  strategy_id?: string;
  params?: Record<string, unknown>;
}

// --------------------------------------------------------------- 建议载荷

/** 硬门槛命中明细。 */
export interface AdviceGate {
  factor_id: string;
  label: string;
  passed: boolean;
  detail: string | null;
}

/** 加分项命中明细。 */
export interface AdviceBonus {
  factor_id: string;
  label: string;
  satisfied: boolean;
}

/** 龙回头结构化建议（`AdviceReportOut.payload` 的具象视图）。 */
export interface DragonAdvicePayload {
  path_id: 'S2' | 'S4' | string;
  path_label: string | null;
  code: string;
  name: string | null;
  buy_day: string | null;
  buy_price: number | null;
  gates: AdviceGate[];
  bonus: AdviceBonus[];
  bonus_score: number | null;
  position: number | null;
  stop_loss_price: number | null;
  sell_timing: string | null;
  field_snapshot: Record<string, unknown>;
}
