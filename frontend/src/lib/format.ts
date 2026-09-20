/**
 * 数值与行情格式化。百分数默认保留 2 位，金额按万/亿自适应。
 */

/** 百分比，单位即百分数值（如 5.32），返回 `+5.32%` / `-5.32%`。 */
export function formatPct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}%`;
}

/** 纯百分比，无正负号。 */
export function formatPercentPlain(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  return `${value.toFixed(digits)}%`;
}

/** 数字千分位分隔。 */
export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** 金额：≥1 亿显示 `x.xx亿`，≥1 万显示 `x.xx万`，否则原值。 */
export function formatAmount(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  const abs = Math.abs(value);
  if (abs >= 1_0000_0000) return `${(value / 1_0000_0000).toFixed(digits)}亿`;
  if (abs >= 1_0000) return `${(value / 1_0000).toFixed(digits)}万`;
  return formatNumber(value, digits);
}

/** 涨跌着色类：配合 A股红涨绿跌的 `stock-up` / `stock-down`。 */
export type PriceTone = 'up' | 'down' | 'flat';
export function priceTone(value: number | null | undefined): PriceTone {
  if (value === null || value === undefined || value === 0) return 'flat';
  return value > 0 ? 'up' : 'down';
}

/** 涨跌类名（用于 Tailwind text 着色）。 */
export function priceToneClass(value: number | null | undefined): string {
  switch (priceTone(value)) {
    case 'up':
      return 'text-stock-up';
    case 'down':
      return 'text-stock-down';
    default:
      return 'text-stock-flat';
  }
}

/** 截断超长字符串。 */
export function truncate(value: string, max = 20): string {
  return value.length > max ? `${value.slice(0, max)}…` : value;
}