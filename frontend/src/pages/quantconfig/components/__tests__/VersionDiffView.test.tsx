/**
 * 版本差异视图：从 fixture 渲染 added / removed / changed 三组参数。
 */

import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { ConfigDiffOut, ParamSpec } from '@/types/config';
import { VersionDiffView } from '../VersionDiffView';

const SPECS: ParamSpec[] = [
  { key: 'base_position', label: '单路基础仓位', type: 'percent', min: 0, max: 1 },
  { key: 'hold_days', label: '持有可卖日数', type: 'int', min: 1, max: 3 },
];

const DIFF: ConfigDiffOut = {
  owner_id: 'dragon',
  from_version: 1,
  to_version: 3,
  added: { hold_days: 2 },
  removed: { gate_shape: '尾盘跳水' },
  changed: { base_position: [0.2, 0.25] },
};

describe('VersionDiffView', () => {
  it('渲染变更（旧值 → 新值）并按类型格式化 percent', () => {
    render(<VersionDiffView diff={DIFF} specs={SPECS} />);

    expect(screen.getByText(/对比 v1 → v3/)).toBeInTheDocument();
    expect(screen.getByText('变更（1）')).toBeInTheDocument();
    // percent 小数口径 0.2 / 0.25 显示为 20% / 25%
    expect(screen.getByText('20%')).toBeInTheDocument();
    expect(screen.getByText('25%')).toBeInTheDocument();
    expect(screen.getByText('base_position')).toBeInTheDocument();
  });

  it('渲染新增与移除参数', () => {
    render(<VersionDiffView diff={DIFF} specs={SPECS} />);

    expect(screen.getByText('新增（1）')).toBeInTheDocument();
    expect(screen.getByText('hold_days')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();

    expect(screen.getByText('移除（1）')).toBeInTheDocument();
    expect(screen.getByText('gate_shape')).toBeInTheDocument();
    expect(screen.getByText('尾盘跳水')).toBeInTheDocument();
  });

  it('无差异时给出一致提示', () => {
    render(
      <VersionDiffView
        diff={{ owner_id: 'dragon', from_version: 1, to_version: 2, added: {}, removed: {}, changed: {} }}
        specs={SPECS}
      />,
    );

    expect(screen.getByText(/v1 与 v2 参数完全一致/)).toBeInTheDocument();
  });
});
