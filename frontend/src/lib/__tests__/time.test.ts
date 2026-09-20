import { describe, expect, it } from 'vitest';
import {
  QUOTE_TZ,
  daysAgoSh,
  fmtDate,
  fmtDateTime,
  hhmm,
  isPastSh,
  isTradingOpen,
  todaySh,
} from '../time';

describe('time（Asia/Shanghai 固定时区）', () => {
  it('QUOTE_TZ 固定为 Asia/Shanghai', () => {
    expect(QUOTE_TZ).toBe('Asia/Shanghai');
  });

  it('跨日边界：UTC 16:00 归到上海次日', () => {
    // 上海 = UTC+8，16:00Z 恰好是次日 00:00
    expect(fmtDate('2026-09-18T16:00:00Z')).toBe('2026-09-19');
    expect(fmtDate('2026-09-18T15:59:59Z')).toBe('2026-09-18');
  });

  it('fmtDateTime 按上海时区渲染（不受本机时区影响）', () => {
    expect(fmtDateTime('2026-09-18T16:00:00Z')).toBe('2026-09-19 00:00:00');
    expect(fmtDateTime('2026-09-18T15:59:59Z')).toBe('2026-09-18 23:59:59');
  });

  it('午夜渲染为 00:00 而非 24:00', () => {
    expect(hhmm('2026-09-18T16:00:00Z')).toBe('00:00');
    expect(hhmm('2026-09-18T01:31:00Z')).toBe('09:31');
  });

  it('todaySh 使用上海日历日', () => {
    expect(todaySh('2026-09-18T16:00:00Z')).toBe('2026-09-19');
    expect(todaySh('2026-09-18T15:00:00Z')).toBe('2026-09-18');
  });

  it('daysAgoSh 按上海日历日回退（跨月正确）', () => {
    expect(daysAgoSh(1, '2026-09-19T01:00:00Z')).toBe('2026-09-18');
    expect(daysAgoSh(30, '2026-03-01T01:00:00Z')).toBe('2026-01-30');
    expect(daysAgoSh(0, '2026-09-19T01:00:00Z')).toBe('2026-09-19');
  });

  it('isTradingOpen 仅在交易日盘中为真', () => {
    // 2026-09-18 为周五；2026-09-19 为周六
    expect(isTradingOpen('2026-09-18T01:40:00Z')).toBe(true); // 09:40 上海
    expect(isTradingOpen('2026-09-18T04:00:00Z')).toBe(false); // 12:00 上海（午休）
    expect(isTradingOpen('2026-09-19T01:40:00Z')).toBe(false); // 周六
  });

  it('isPastSh 用于有效期判定', () => {
    expect(isPastSh('2026-09-18T00:00:00Z', '2026-09-19T00:00:00Z')).toBe(true);
    expect(isPastSh('2026-09-20T00:00:00Z', '2026-09-19T00:00:00Z')).toBe(false);
  });
});
