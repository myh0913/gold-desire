/**
 * 参数草稿：以 schema 默认值打底叠加生效参数，提供逐键编辑、校验与脏检查。
 *
 * 组件**不**监听 `activeParams` 变化做隐式重置；调用方通过给详情组件加
 * `key`（切换对象/版本）来触发重挂载，避免编辑态被意外覆盖。
 */

import { useCallback, useMemo, useState } from 'react';
import type { ParamSpec } from '@/types/config';
import { isSameParams, mergeDefaults, validateParams } from '../lib/paramSchema';

export interface ParamDraftState {
  values: Record<string, unknown>;
  setValue: (key: string, value: unknown) => void;
  /** 以给定参数（缺省为原始生效参数）重置草稿 */
  reset: (next?: Record<string, unknown>) => void;
  errors: Record<string, string>;
  isValid: boolean;
  dirty: boolean;
}

export function useParamDraft(
  specs: readonly ParamSpec[],
  activeParams: Record<string, unknown>,
  enumOptions: Record<string, string[]> = {},
): ParamDraftState {
  const baseline = useMemo(() => mergeDefaults(specs, activeParams), [specs, activeParams]);
  const [values, setValues] = useState<Record<string, unknown>>(() => baseline);

  const setValue = useCallback((key: string, value: unknown) => {
    setValues((previous) => ({ ...previous, [key]: value }));
  }, []);

  const reset = useCallback(
    (next?: Record<string, unknown>) => {
      setValues(next ? mergeDefaults(specs, next) : baseline);
    },
    [specs, baseline],
  );

  const errors = useMemo(() => validateParams(specs, values, enumOptions), [specs, values, enumOptions]);
  const dirty = useMemo(() => !isSameParams(specs, values, baseline), [specs, values, baseline]);

  return {
    values,
    setValue,
    reset,
    errors,
    isValid: Object.keys(errors).length === 0,
    dirty,
  };
}
