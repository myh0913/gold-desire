/**
 * 行情域契约（总览页所需子集）：情绪指标与历史。
 *
 * 逐字段镜像后端 `app/schemas/market.py` / `app/schemas/common.py`，
 * 字段名保持 snake_case。比率字段为**小数口径**（`0.42 = 42%`）。
 *
 * **注意**：本文件**不**从 `types/index.ts` 导出（该出口由其他 Task 维护），
 * 使用方一律 `import type { ... } from '@/types/market'`。
 */

/** 行情数据新鲜度标记（后端 `MarketMeta`）。 */
export interface MarketMeta {
  /** 数据是否已超出预期新鲜度窗口（上游不可用时的降级返回） */
  stale: boolean;
  /** 库中最新数据日期 */
  data_date: string | null;
}

/** 市场情绪指标。 */
export interface SentimentOut {
  trade_date: string;
  /** 情绪温度（0~100 分） */
  temperature: number;
  /** 情绪周期阶段（冰点 / 冰点转折 / 修复 / 加速/高潮 / 分歧 / 退潮）；未派生时为 null */
  stage: string | null;
  limit_up_count: number;
  limit_down_count: number;
  broken_board_count: number;
  /** 炸板率（小数口径） */
  broken_rate: number;
  up_count: number;
  down_count: number;
  max_continue_days: number | null;
  /** 昨日涨停今日平均溢价（小数口径） */
  premium_rate: number;
}

/** 某交易日情绪指标。 */
export interface SentimentResponse extends MarketMeta {
  trade_date: string | null;
  item: SentimentOut | null;
}

/** 最近 N 个交易日情绪（按交易日升序）。 */
export interface SentimentHistoryResponse extends MarketMeta {
  days: number;
  items: SentimentOut[];
}
