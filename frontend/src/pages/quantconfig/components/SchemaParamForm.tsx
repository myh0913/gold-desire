/**
 * 可复用「schema 驱动参数表单」（策略页与因子页共用）。
 *
 * 按后端下发的 `params_schema.params` 渲染控件：
 *
 * - `percent`：以**百分数**显示与编辑（`0.08` ↔ `8`），提交值仍为小数口径；
 * - `float` / `int`：数字输入，`step` 取自 schema；
 * - `bool`：复选框；
 * - `enum`：有候选值时用下拉框（候选取自因子 `buckets[].equals`），否则自由文本。
 *
 * 校验由 `lib/paramSchema.ts` 的 `validateParam` 负责，本组件只负责展示错误；
 * 「是否允许提交」由调用方依据 `errors` / `isValid` 决定。
 */

import { useEffect, useState, type ReactNode } from 'react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { cn } from '@/lib/utils';
import type { ParamSpec } from '@/types/config';
import { displayToPercent, percentToDisplay, round6 } from '../lib/paramSchema';

export interface SchemaParamFormProps {
  specs: readonly ParamSpec[];
  /** 当前参数值（percent 为小数口径） */
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  errors?: Record<string, string>;
  disabled?: boolean;
  /** enum 候选值（key → 候选项）；缺省时 enum 渲染为自由文本 */
  enumOptions?: Record<string, string[]>;
  idPrefix?: string;
}

function toText(spec: ParamSpec, value: unknown): string {
  if (value === null || value === undefined || value === '') return '';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '';
  if (spec.type === 'percent') {
    const display = percentToDisplay(numeric);
    return display === null ? '' : String(display);
  }
  return String(numeric);
}

function parseText(spec: ParamSpec, text: string): number | null {
  const trimmed = text.trim();
  if (trimmed === '') return null;
  const numeric = Number(trimmed);
  if (!Number.isFinite(numeric)) return null;
  return spec.type === 'percent' ? displayToPercent(numeric) : numeric;
}

function stepOf(spec: ParamSpec): number | 'any' {
  if (spec.type === 'percent') {
    return spec.step === null || spec.step === undefined ? 1 : round6(spec.step * 100);
  }
  if (spec.type === 'int') return 1;
  return spec.step === null || spec.step === undefined ? 'any' : spec.step;
}

/** 数值字段：本地保留输入文本，避免输入中间态（`-`、`0.`）被外部格式化打断。 */
function NumberField({
  spec,
  value,
  disabled,
  invalid,
  id,
  onChange,
}: {
  spec: ParamSpec;
  value: unknown;
  disabled: boolean;
  invalid: boolean;
  id: string;
  onChange: (value: unknown) => void;
}) {
  const [text, setText] = useState(() => toText(spec, value));

  useEffect(() => {
    setText((current) => (parseText(spec, current) === value ? current : toText(spec, value)));
  }, [spec, value]);

  return (
    <Input
      id={id}
      type="number"
      inputMode="decimal"
      className={cn('h-9', invalid && 'border-destructive')}
      value={text}
      step={stepOf(spec)}
      disabled={disabled}
      aria-invalid={invalid}
      onChange={(event) => {
        const next = event.target.value;
        setText(next);
        onChange(parseText(spec, next));
      }}
    />
  );
}

function FieldRow({
  spec,
  children,
  error,
  htmlFor,
}: {
  spec: ParamSpec;
  children: ReactNode;
  error?: string;
  htmlFor: string;
}) {
  const bounds: string[] = [];
  if (spec.min !== null && spec.min !== undefined) {
    bounds.push(
      `≥ ${spec.type === 'percent' ? `${round6(spec.min * 100)}%` : String(spec.min)}`,
    );
  }
  if (spec.max !== null && spec.max !== undefined) {
    bounds.push(
      `≤ ${spec.type === 'percent' ? `${round6(spec.max * 100)}%` : String(spec.max)}`,
    );
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <Label htmlFor={htmlFor}>{spec.label ?? spec.key}</Label>
        <span className="text-muted-foreground font-mono text-[10px]">
          {spec.key}
          {spec.unit ? ` · ${spec.unit}` : ''}
        </span>
      </div>
      {children}
      <div className="text-muted-foreground flex flex-wrap gap-x-3 text-[11px]">
        {bounds.length > 0 && <span>范围 {bounds.join(' ~ ')}</span>}
        {spec.description && <span className="min-w-0">{spec.description}</span>}
      </div>
      {error && (
        <p role="alert" className="text-destructive text-[11px]">
          {error}
        </p>
      )}
    </div>
  );
}

export function SchemaParamForm({
  specs,
  values,
  onChange,
  errors = {},
  disabled = false,
  enumOptions = {},
  idPrefix = 'param',
}: SchemaParamFormProps) {
  if (specs.length === 0) {
    return <p className="text-muted-foreground text-sm">该对象未声明可配置参数。</p>;
  }

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      {specs.map((spec) => {
        const id = `${idPrefix}-${spec.key}`;
        const invalid = Boolean(errors[spec.key]);
        const value = values[spec.key];

        if (spec.type === 'bool') {
          return (
            <FieldRow key={spec.key} spec={spec} error={errors[spec.key]} htmlFor={id}>
              <label
                htmlFor={id}
                className="border-input flex h-9 items-center gap-2 rounded-md border px-3 text-sm"
              >
                <input
                  id={id}
                  type="checkbox"
                  className="size-4"
                  checked={value === true}
                  disabled={disabled}
                  onChange={(event) => onChange(spec.key, event.target.checked)}
                />
                <span className="text-muted-foreground">{value === true ? '开启' : '关闭'}</span>
              </label>
            </FieldRow>
          );
        }

        if (spec.type === 'enum') {
          const options = enumOptions[spec.key] ?? [];
          return (
            <FieldRow key={spec.key} spec={spec} error={errors[spec.key]} htmlFor={id}>
              {options.length > 0 ? (
                <Select
                  id={id}
                  value={typeof value === 'string' ? value : ''}
                  disabled={disabled}
                  aria-invalid={invalid}
                  onChange={(event) => onChange(spec.key, event.target.value)}
                >
                  {options.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </Select>
              ) : (
                <Input
                  id={id}
                  value={typeof value === 'string' ? value : ''}
                  disabled={disabled}
                  aria-invalid={invalid}
                  className={cn(invalid && 'border-destructive')}
                  onChange={(event) => onChange(spec.key, event.target.value)}
                />
              )}
            </FieldRow>
          );
        }

        return (
          <FieldRow key={spec.key} spec={spec} error={errors[spec.key]} htmlFor={id}>
            <div className="flex items-center gap-2">
              <NumberField
                spec={spec}
                value={value}
                disabled={disabled}
                invalid={invalid}
                id={id}
                onChange={(next) => onChange(spec.key, next)}
              />
              {spec.type === 'percent' && (
                <span className="text-muted-foreground text-sm">%</span>
              )}
            </div>
          </FieldRow>
        );
      })}
    </div>
  );
}
