/**
 * 时间处理：全站唯一入口，固定 `Asia/Shanghai`，禁止本地时区泄漏。
 *
 * 约定：
 *
 * - 组件**不得**自行 `new Date()` 后拼接格式，一律调用本模块（或 `nowSh()` 取当前瞬时）。
 * - 所有格式化显式指定 `timeZone: 'Asia/Shanghai'` + `hourCycle: 'h23'`，
 *   不依赖运行环境 locale / 本地时区（原项目此处混用本地时区，导致跨时区日期错位）。
 */

export const QUOTE_TZ = 'Asia/Shanghai';

const shFormatter = new Intl.DateTimeFormat('en-CA', {
  timeZone: QUOTE_TZ,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
});

interface ShParts {
  year: string;
  month: string;
  day: string;
  hour: string;
  minute: string;
  second: string;
}

function toShParts(input: string | number | Date): ShParts {
  const date = input instanceof Date ? input : new Date(input);
  const raw: Record<string, string> = {};
  for (const part of shFormatter.formatToParts(date)) {
    if (part.type !== 'literal') raw[part.type] = part.value;
  }
  return {
    year: raw.year,
    month: raw.month,
    day: raw.day,
    hour: raw.hour,
    minute: raw.minute,
    second: raw.second,
  };
}

/** 当前瞬时。作为「取当前时间」的唯一入口，便于测试与统一审计。 */
export function nowSh(): Date {
  return new Date();
}

/** 上海时区下的日历日 `YYYY-MM-DD`。 */
export function fmtDate(input: string | number | Date): string {
  const p = toShParts(input);
  return `${p.year}-${p.month}-${p.day}`;
}

/** 上海时区下的完整时间 `YYYY-MM-DD HH:mm:ss`。 */
export function fmtDateTime(input: string | number | Date): string {
  const p = toShParts(input);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second}`;
}

/** 上海时区下的 `HH:mm`。 */
export function hhmm(input: string | number | Date): string {
  const p = toShParts(input);
  return `${p.hour}:${p.minute}`;
}

/** 上海时区下的今天 `YYYY-MM-DD`。 */
export function todaySh(from: string | number | Date = nowSh()): string {
  return fmtDate(from);
}

/**
 * 上海时区下 N 天前的日历日 `YYYY-MM-DD`。
 *
 * 先取上海日历日，再按 UTC 日历做减法，避免夏令时/本地时区参与运算。
 */
export function daysAgoSh(days: number, from: string | number | Date = nowSh()): string {
  const [year, month, day] = fmtDate(from).split('-').map(Number);
  const shifted = new Date(Date.UTC(year, month - 1, day) - days * 86_400_000);
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}`;
}

/** 是否处于 A 股交易时段（上海时区工作日 09:30–11:30 / 13:00–15:00）。 */
export function isTradingOpen(input: string | number | Date = nowSh()): boolean {
  const p = toShParts(input);
  const weekday = new Date(`${p.year}-${p.month}-${p.day}T00:00:00Z`).getUTCDay();
  if (weekday === 0 || weekday === 6) return false;
  const minutes = Number(p.hour) * 60 + Number(p.minute);
  return (minutes >= 570 && minutes < 690) || (minutes >= 780 && minutes < 900);
}

/** 是否已早于当前瞬时（有效期/过期判定）。 */
export function isPastSh(
  input: string | number | Date,
  reference: string | number | Date = nowSh(),
): boolean {
  const value = input instanceof Date ? input.getTime() : new Date(input).getTime();
  const base = reference instanceof Date ? reference.getTime() : new Date(reference).getTime();
  return value < base;
}
