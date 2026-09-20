/**
 * 参数 schema 工具：解析、percent 小数↔显示换算、客户端范围校验。
 *
 * 口径约定（与后端 `app/factors/base.py::FactorParamSpec` 一致）：
 *
 * - `percent` 参数**以小数存储**（`0.08 = 8%`），`min` / `max` 同为小数口径；
 *   表单显示与编辑一律乘以 100（8 表示 8%），提交前再除回小数。
 * - `enum` 参数后端**未**下发候选值，候选取自因子 `buckets[].equals`；
 *   取不到时候退为自由文本输入（不臆造候选）。
 */

import type { BucketDef, ParamSpec, ParamType, ParamsSchema } from '@/types/config';

const TYPES: readonly ParamType[] = ['float', 'int', 'bool', 'percent', 'enum'];

/** 保留 6 位小数，消除 `0.08 * 100` 之类浮点尾差。 */
export function round6(value: number): number {
  return Math.round(value * 1e6) / 1e6;
}

function normalizeType(value: unknown): ParamType {
  return TYPES.includes(value as ParamType) ? (value as ParamType) : 'float';
}

/** 从 `params_schema` 取出参数声明列表（缺省为空）。 */
export function parseParamSpecs(schema: ParamsSchema | undefined | null): ParamSpec[] {
  const raw = schema?.params;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is ParamSpec => Boolean(item) && typeof item.key === 'string')
    .map((item) => ({ ...item, type: normalizeType(item.type) }));
}

/** 从档位声明派生 enum 候选值（去重、保持声明顺序）。 */
export function deriveEnumOptions(buckets: readonly BucketDef[] | undefined): string[] {
  const options: string[] = [];
  for (const bucket of buckets ?? []) {
    const value = bucket?.equals;
    if (typeof value === 'string' && value && !options.includes(value)) options.push(value);
  }
  return options;
}

/** 小数口径 → 显示值（`0.08` → `8`）；非数值返回 `null`。 */
export function percentToDisplay(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return round6(numeric * 100);
}

/** 显示值 → 小数口径（`8` → `0.08`）。 */
export function displayToPercent(display: number): number {
  return round6(display / 100);
}

/** 单个参数的显示文本（用于版本历史 / diff 等只读场景）。 */
export function formatParamValue(spec: ParamSpec, value: unknown): string {
  if (value === null || value === undefined) return '--';
  switch (spec.type) {
    case 'percent': {
      const display = percentToDisplay(value);
      return display === null ? '--' : `${display}%`;
    }
    case 'bool':
      return value === true ? '是' : '否';
    default:
      return String(value);
  }
}

/** 校验单个参数；合法返回 `null`，否则返回中文错误信息。 */
export function validateParam(
  spec: ParamSpec,
  value: unknown,
  enumOptions?: readonly string[],
): string | null {
  const label = spec.label ?? spec.key;

  if (spec.type === 'bool') {
    return typeof value === 'boolean' ? null : `${label} 需为布尔值`;
  }

  if (spec.type === 'enum') {
    if (typeof value !== 'string' || value === '') return `${label} 不能为空`;
    if (enumOptions && enumOptions.length > 0 && !enumOptions.includes(value)) {
      return `${label} 取值不在候选范围内`;
    }
    return null;
  }

  if (value === null || value === undefined || value === '') return `${label} 不能为空`;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return `${label} 需为数字`;

  if (spec.type === 'int' && !Number.isInteger(numeric)) return `${label} 需为整数`;

  const suffix = spec.type === 'percent' ? '%' : '';
  const scale = spec.type === 'percent' ? 100 : 1;
  if (spec.min !== null && spec.min !== undefined && numeric < spec.min) {
    return `${label} 不得小于 ${round6(spec.min * scale)}${suffix}`;
  }
  if (spec.max !== null && spec.max !== undefined && numeric > spec.max) {
    return `${label} 不得大于 ${round6(spec.max * scale)}${suffix}`;
  }
  return null;
}

/** 校验整包参数；返回 `{ key: 错误信息 }`（无错误时为空对象）。 */
export function validateParams(
  specs: readonly ParamSpec[],
  values: Record<string, unknown>,
  enumOptions: Record<string, string[]> = {},
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const spec of specs) {
    const message = validateParam(spec, values[spec.key], enumOptions[spec.key]);
    if (message) errors[spec.key] = message;
  }
  return errors;
}

/** 以 schema 默认值打底，再叠加生效参数（仅覆盖 schema 中声明的键）。 */
export function mergeDefaults(
  specs: readonly ParamSpec[],
  params: Record<string, unknown> | undefined,
): Record<string, unknown> {
  const merged: Record<string, unknown> = {};
  for (const spec of specs) {
    merged[spec.key] = spec.default ?? null;
  }
  for (const spec of specs) {
    if (params && Object.prototype.hasOwnProperty.call(params, spec.key)) {
      merged[spec.key] = params[spec.key];
    }
  }
  return merged;
}

function sameValue(a: unknown, b: unknown): boolean {
  if (typeof a === 'number' && typeof b === 'number') return round6(a) === round6(b);
  return a === b;
}

/** 两份参数是否等价（数值按 6 位小数比较）。 */
export function isSameParams(
  specs: readonly ParamSpec[],
  a: Record<string, unknown>,
  b: Record<string, unknown>,
): boolean {
  return specs.every((spec) => sameValue(a[spec.key], b[spec.key]));
}
