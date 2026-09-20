/**
 * SchemaParamForm：各参数类型渲染、percent 小数↔显示换算、min/max 校验。
 */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import type { ParamSpec } from '@/types/config';
import {
  displayToPercent,
  percentToDisplay,
  validateParam,
  validateParams,
} from '../../lib/paramSchema';
import { SchemaParamForm } from '../SchemaParamForm';

const SPECS: ParamSpec[] = [
  {
    key: 'base_position',
    label: '单路基础仓位',
    type: 'percent',
    default: 0.2,
    min: 0,
    max: 1,
    step: 0.01,
    unit: '小数',
  },
  { key: 'hold_days', label: '持有可卖日数', type: 'int', default: 1, min: 1, max: 3, step: 1 },
  { key: 'scale_by_bonus', label: '按加分项加码', type: 'bool', default: true },
  { key: 'gate_shape', label: '硬门槛形态', type: 'enum', default: '尾盘跳水' },
];

const ENUM_OPTIONS = { gate_shape: ['尾盘跳水', '冲高回落'] };

describe('paramSchema 换算与校验', () => {
  it('percent 在小数口径与显示百分数之间往返换算', () => {
    expect(percentToDisplay(0.08)).toBe(8);
    expect(percentToDisplay(-0.03)).toBe(-3);
    expect(displayToPercent(8)).toBe(0.08);
    expect(displayToPercent(-3)).toBe(-0.03);
    expect(percentToDisplay(displayToPercent(7))).toBe(7);
    expect(percentToDisplay(null)).toBeNull();
  });

  it('percent 的 min/max 按小数口径校验并给出百分数提示', () => {
    const spec = SPECS[0];
    expect(validateParam(spec, 0.2)).toBeNull();
    expect(validateParam(spec, 1.2)).toBe('单路基础仓位 不得大于 100%');
    expect(validateParam(spec, -0.1)).toBe('单路基础仓位 不得小于 0%');
    expect(validateParam(spec, null)).toBe('单路基础仓位 不能为空');
  });

  it('int 拒绝非整数，enum 拒绝候选外取值', () => {
    expect(validateParam(SPECS[1], 1.5)).toBe('持有可卖日数 需为整数');
    expect(validateParam(SPECS[1], 4)).toBe('持有可卖日数 不得大于 3');
    expect(validateParam(SPECS[3], '尾盘跳水', ENUM_OPTIONS.gate_shape)).toBeNull();
    expect(validateParam(SPECS[3], '不存在的形态', ENUM_OPTIONS.gate_shape)).toBe(
      '硬门槛形态 取值不在候选范围内',
    );
  });

  it('validateParams 汇总全部非法参数', () => {
    const errors = validateParams(SPECS, {
      base_position: 2,
      hold_days: 1,
      scale_by_bonus: 'yes',
      gate_shape: '尾盘跳水',
    });
    expect(Object.keys(errors).sort()).toEqual(['base_position', 'scale_by_bonus']);
  });
});

describe('SchemaParamForm 渲染', () => {
  it('percent 以百分数显示，编辑后回调小数口径', () => {
    const onChange = vi.fn();
    render(
      <SchemaParamForm
        specs={SPECS}
        values={{ base_position: 0.2, hold_days: 1, scale_by_bonus: true, gate_shape: '尾盘跳水' }}
        onChange={onChange}
        enumOptions={ENUM_OPTIONS}
      />,
    );

    const percentInput = screen.getByLabelText('单路基础仓位');
    expect(percentInput).toHaveValue(20);

    fireEvent.change(percentInput, { target: { value: '8' } });
    expect(onChange).toHaveBeenCalledWith('base_position', 0.08);
  });

  it('bool 渲染为复选框并可切换', () => {
    const onChange = vi.fn();
    render(
      <SchemaParamForm
        specs={SPECS}
        values={{ base_position: 0.2, hold_days: 1, scale_by_bonus: true, gate_shape: '尾盘跳水' }}
        onChange={onChange}
        enumOptions={ENUM_OPTIONS}
      />,
    );

    const checkbox = screen.getByRole('checkbox');
    expect(checkbox).toBeChecked();
    fireEvent.click(checkbox);
    expect(onChange).toHaveBeenCalledWith('scale_by_bonus', false);
  });

  it('enum 有候选值时渲染下拉框并可切换', () => {
    const onChange = vi.fn();
    render(
      <SchemaParamForm
        specs={SPECS}
        values={{ base_position: 0.2, hold_days: 1, scale_by_bonus: true, gate_shape: '尾盘跳水' }}
        onChange={onChange}
        enumOptions={ENUM_OPTIONS}
      />,
    );

    const select = screen.getByLabelText('硬门槛形态');
    expect(select.tagName).toBe('SELECT');
    expect(screen.getByRole('option', { name: '冲高回落' })).toBeInTheDocument();

    fireEvent.change(select, { target: { value: '冲高回落' } });
    expect(onChange).toHaveBeenCalledWith('gate_shape', '冲高回落');
  });

  it('enum 无候选值时退化为自由文本输入（不臆造候选）', () => {
    const onChange = vi.fn();
    render(
      <SchemaParamForm specs={[SPECS[3]]} values={{ gate_shape: '尾盘跳水' }} onChange={onChange} />,
    );

    const input = screen.getByLabelText('硬门槛形态');
    expect(input.tagName).toBe('INPUT');
    fireEvent.change(input, { target: { value: '冲高回落' } });
    expect(onChange).toHaveBeenCalledWith('gate_shape', '冲高回落');
  });

  it('展示校验错误与范围提示', () => {
    render(
      <SchemaParamForm
        specs={SPECS}
        values={{ base_position: 2, hold_days: 1, scale_by_bonus: true, gate_shape: '尾盘跳水' }}
        onChange={vi.fn()}
        errors={{ base_position: '单路基础仓位 不得大于 100%' }}
        enumOptions={ENUM_OPTIONS}
      />,
    );

    expect(screen.getByText('单路基础仓位 不得大于 100%')).toBeInTheDocument();
    expect(screen.getByText(/范围 ≥ 0% ~ ≤ 100%/)).toBeInTheDocument();
  });
});
