/**
 * 观察要点：由情绪指标**本地计算**的简短点评（不引入任何后端未提供的数据）。
 *
 * 口径沿用旧前端总览页的思路并重写：温度冷热 → 涨停家数扩散度 → 炸板率风险 →
 * 昨日涨停今日平均溢价（正/负反馈）。
 */

import { formatPct, formatPercentPlain } from '@/lib/format';
import type { SentimentOut } from '@/types/market';

export type ObservationTone = 'plain' | 'warn' | 'up' | 'down';

export interface ObservationSegment {
  tone: ObservationTone;
  text: string;
}

/** 温度分档阈值（温度 < 30 偏冷、≥ 50 偏热，中间为中性区间）。 */
export function buildObservations(sentiment: SentimentOut): ObservationSegment[] {
  const segments: ObservationSegment[] = [];

  segments.push({
    tone: 'plain',
    text: `当前市场处于${sentiment.temperature >= 50 ? '偏热' : '偏冷'}区间（温度 ${sentiment.temperature.toFixed(1)}）。`,
  });

  if (sentiment.limit_up_count >= 60) {
    segments.push({ tone: 'plain', text: '涨停扩散明显，赚钱效应强。' });
  } else if (sentiment.limit_up_count >= 30) {
    segments.push({ tone: 'plain', text: '涨停家数正常，情绪平稳。' });
  } else {
    segments.push({ tone: 'plain', text: '涨停家数偏少，警惕情绪转弱。' });
  }

  if (sentiment.broken_rate >= 0.4) {
    segments.push({
      tone: 'warn',
      text: `炸板率 ${formatPercentPlain(sentiment.broken_rate * 100, 0)} 偏高，接力需谨慎。`,
    });
  } else {
    segments.push({
      tone: 'plain',
      text: `炸板率 ${formatPercentPlain(sentiment.broken_rate * 100, 0)}，封板质量尚可。`,
    });
  }

  if (sentiment.premium_rate > 0) {
    segments.push({
      tone: 'up',
      text: `昨日涨停今日平均溢价 ${formatPct(sentiment.premium_rate * 100)}，正反馈。`,
    });
  } else if (sentiment.premium_rate < 0) {
    segments.push({
      tone: 'down',
      text: `昨日涨停今日平均溢价 ${formatPct(sentiment.premium_rate * 100)}，负反馈。`,
    });
  }

  return segments;
}
