/**
 * 策略 id 展示映射：建议卡徽标 / 复盘回溯 / 策略战绩共用。
 * 未知 id 回退原值（新增策略无需改前端）。
 */

export const STRATEGY_LABELS: Record<string, string> = {
  dragon: '龙回头',
  firstboard_dip: '首板低吸',
  lianban: '连板',
  auction_grab: '竞价抢筹',
};

export const strategyLabel = (id: string): string => STRATEGY_LABELS[id] ?? id;
