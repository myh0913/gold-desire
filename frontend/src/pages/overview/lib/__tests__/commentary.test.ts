/**
 * 观察要点本地推导：热市 vs 冷市两份 fixture 的文案分叉。
 */

import { describe, expect, it } from 'vitest';
import { buildObservations } from '../commentary';
import type { SentimentOut } from '@/types/market';

const fixture = (overrides: Partial<SentimentOut>): SentimentOut => ({
  trade_date: '2026-09-18',
  temperature: 50,
  stage: '修复',
  limit_up_count: 40,
  limit_down_count: 5,
  broken_board_count: 20,
  broken_rate: 0.2,
  up_count: 2000,
  down_count: 2000,
  max_continue_days: 4,
  premium_rate: 0,
  ...overrides,
});

const textOf = (segments: ReturnType<typeof buildObservations>) =>
  segments.map((segment) => segment.text).join('');

describe('buildObservations', () => {
  it('热市：偏热 + 涨停扩散 + 高炸板率警示 + 溢价正反馈', () => {
    const segments = buildObservations(
      fixture({
        temperature: 82,
        limit_up_count: 88,
        broken_rate: 0.5,
        premium_rate: 0.032,
      }),
    );

    expect(textOf(segments)).toContain('偏热区间（温度 82.0）');
    expect(textOf(segments)).toContain('涨停扩散明显，赚钱效应强');
    expect(textOf(segments)).toContain('炸板率 50% 偏高，接力需谨慎');
    expect(textOf(segments)).toContain('昨日涨停今日平均溢价 +3.20%，正反馈');
    // 高炸板率带警示色
    expect(segments.find((s) => s.text.includes('炸板率 50%'))?.tone).toBe('warn');
    expect(segments.find((s) => s.text.includes('正反馈'))?.tone).toBe('up');
  });

  it('冷市：偏冷 + 涨停偏少 + 炸板率尚可 + 溢价负反馈', () => {
    const segments = buildObservations(
      fixture({
        temperature: 15,
        limit_up_count: 12,
        broken_rate: 0.18,
        premium_rate: -0.018,
      }),
    );

    expect(textOf(segments)).toContain('偏冷区间（温度 15.0）');
    expect(textOf(segments)).toContain('涨停家数偏少，警惕情绪转弱');
    expect(textOf(segments)).toContain('炸板率 18%，封板质量尚可');
    expect(textOf(segments)).toContain('昨日涨停今日平均溢价 -1.80%，负反馈');
    expect(segments.find((s) => s.text.includes('负反馈'))?.tone).toBe('down');
  });

  it('中性样本：溢价为 0 时不产出正/负反馈段', () => {
    const segments = buildObservations(fixture({ temperature: 50, premium_rate: 0 }));
    expect(textOf(segments)).not.toContain('正反馈');
    expect(textOf(segments)).not.toContain('负反馈');
    expect(textOf(segments)).toContain('涨停家数正常，情绪平稳');
  });
});
