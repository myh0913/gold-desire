/**
 * 因子有效性：必须渲染 A/B/C 分段列（守住「不得只报聚合」的硬要求）。
 */

import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { FactorEffectivenessResponse } from '@/types/config';
import { FactorEffectiveness } from '../FactorEffectiveness';

const DATA: FactorEffectivenessResponse = {
  factor_id: 'first_yin_amplitude',
  params_version: 'v3',
  cuts: ['2026-01-13', '2026-05-29'],
  sample_count: 300,
  buckets: [
    {
      bucket: '<=5%',
      n: 120,
      mean_return: -0.004,
      win_rate: 0.42,
      segments: {
        A: { n: 40, mean_return: -0.002, win_rate: 0.45 },
        B: { n: 40, mean_return: -0.006, win_rate: 0.4 },
        C: { n: 40, mean_return: -0.004, win_rate: 0.41 },
      },
    },
    {
      bucket: '>=8%',
      n: 180,
      mean_return: 0.025,
      win_rate: 0.58,
      segments: {
        A: { n: 60, mean_return: 0.03, win_rate: 0.6 },
        B: { n: 60, mean_return: 0.026, win_rate: 0.57 },
        C: { n: 60, mean_return: 0.019, win_rate: 0.56 },
      },
    },
  ],
};

describe('FactorEffectiveness', () => {
  it('同时渲染全量列与 A / B / C 分段列', () => {
    render(<FactorEffectiveness data={DATA} />);

    expect(screen.getByRole('columnheader', { name: '档位' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /A 段/ })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /B 段/ })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /C 段/ })).toBeInTheDocument();
  });

  it('渲染每段的样本数、期望与胜率，而非仅聚合值', () => {
    render(<FactorEffectiveness data={DATA} />);

    // >=8% 档位 A 段：n=60 · +3.00% · +60.0%（文本拆分为多个着色 span，分别断言）
    expect(screen.getAllByText(/n=60/).length).toBe(3);
    expect(screen.getByText('+3.00%')).toBeInTheDocument();
    expect(screen.getByText(/\+60\.0%/)).toBeInTheDocument();
    // <=5% 档位 C 段：n=40 · -0.40% · +41.0%
    expect(screen.getAllByText(/n=40/).length).toBe(3);
    expect(screen.getAllByText('-0.40%').length).toBe(2);
    expect(screen.getByText(/\+41\.0%/)).toBeInTheDocument();
    // 档位行本身
    expect(screen.getByText('>=8%')).toBeInTheDocument();
    expect(screen.getByText('<=5%')).toBeInTheDocument();
  });

  it('期望收益按涨跌着色并渲染单调性比例条', () => {
    const { container } = render(<FactorEffectiveness data={DATA} />);

    // A 股红涨绿跌：+2.50%（全量最大正）为红，-0.60% 为绿
    expect(screen.getByText('+2.50%')).toHaveClass('text-stock-up');
    expect(screen.getByText('-0.60%')).toHaveClass('text-stock-down');
    // 每个收益单元格都带比例条（2 档 × (全量 + 3 段) = 8 条）
    expect(container.querySelectorAll('.bg-stock-up, .bg-stock-down').length).toBe(8);
  });

  it('提示 A/B/C 切点为快照且全量单调 ≠ 分段单调', () => {
    render(<FactorEffectiveness data={DATA} />);

    expect(screen.getByText(/A\/B\/C 切点快照/)).toBeInTheDocument();
    expect(screen.getByText(/2026-01-13/)).toBeInTheDocument();
    expect(screen.getByText(/全量单调 ≠ 分段单调/)).toBeInTheDocument();
  });
});
