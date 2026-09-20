import { describe, expect, it } from 'vitest';
import {
  formatAmount,
  formatNumber,
  formatPct,
  formatPercentPlain,
  priceTone,
  priceToneClass,
} from '../format';

describe('format', () => {
  it('百分比带符号', () => {
    expect(formatPct(5.321)).toBe('+5.32%');
    expect(formatPct(-0.5)).toBe('-0.50%');
    expect(formatPct(0)).toBe('0.00%');
    expect(formatPct(null)).toBe('--');
    expect(formatPercentPlain(5.321)).toBe('5.32%');
  });

  it('金额按万/亿自适应', () => {
    expect(formatAmount(999)).toBe('999.00');
    expect(formatAmount(12_345)).toBe('1.23万');
    expect(formatAmount(123_456_789)).toBe('1.23亿');
    expect(formatAmount(-12_345)).toBe('-1.23万');
    expect(formatAmount(undefined)).toBe('--');
  });

  it('数字千分位', () => {
    expect(formatNumber(1_234_567)).toBe('1,234,567');
    expect(formatNumber(1234.5, 1)).toBe('1,234.5');
  });

  it('A 股红涨绿跌', () => {
    expect(priceTone(1)).toBe('up');
    expect(priceTone(-1)).toBe('down');
    expect(priceTone(0)).toBe('flat');
    expect(priceTone(null)).toBe('flat');
    expect(priceToneClass(1)).toBe('text-stock-up');
    expect(priceToneClass(-1)).toBe('text-stock-down');
    expect(priceToneClass(0)).toBe('text-stock-flat');
  });
});
