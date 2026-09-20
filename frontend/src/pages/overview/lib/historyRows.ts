/**
 * 情绪历史行映射：后端 `SentimentOut[]` → 三张走势图共用的行结构。
 *
 * 溢价转为百分数（后端为小数口径）；日期同时保留展示值（MM-DD）与全量值（tooltip 用）。
 */

import { fmtDate } from '@/lib/time';
import type { SentimentOut } from '@/types/market';

/** 单个走势图数据行。 */
export interface SentimentHistoryRow {
  /** 横轴展示（MM-DD） */
  date: string;
  /** 完整日期（tooltip / 区间说明用） */
  fullDate: string;
  /** 情绪温度（0~100） */
  temperature: number;
  /** 昨日涨停今日平均溢价（百分数，如 1.23 表示 +1.23%） */
  premiumPct: number;
  limitUp: number;
  limitDown: number;
  broken: number;
  up: number;
  down: number;
}

/** 行映射（升序输入 → 升序输出）。 */
export function toHistoryRows(items: SentimentOut[]): SentimentHistoryRow[] {
  return items.map((item) => {
    const fullDate = fmtDate(item.trade_date);
    return {
      date: fullDate.slice(5),
      fullDate,
      temperature: Number(item.temperature.toFixed(1)),
      premiumPct: Number((item.premium_rate * 100).toFixed(2)),
      limitUp: item.limit_up_count,
      limitDown: item.limit_down_count,
      broken: item.broken_board_count,
      up: item.up_count,
      down: item.down_count,
    };
  });
}
