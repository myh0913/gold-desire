/**
 * 今日建议页冒烟（mock fetch）：建议卡片渲染（路次/门槛/仓位/止损）与日期下拉。
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { createQueryWrapper, mockFetch } from '@/test/queryWrapper';
import AdvicePage from '@/pages/advice/AdvicePage';

const ADVICE = {
  trade_date: '2026-06-03',
  kind: 'advice',
  strategy_id: 'dragon',
  strategy_version: null,
  ran_at: '2026-06-03T15:05:00Z',
  created_at: null,
  payload: {
    path_id: 'S2',
    path_label: '高位跳水型',
    code: '600001',
    name: '测试一号',
    buy_day: '2026-06-04',
    buy_price: 11.6,
    gates: [
      { factor_id: 'shape', label: '首阴形态=尾盘跳水', passed: true, detail: '尾盘跳水' },
      { factor_id: 'amp', label: '首阴振幅≥8%', passed: true, detail: '9.2%' },
    ],
    bonus: [{ factor_id: 'boards', label: '连板≥3', satisfied: true }],
    bonus_score: 1,
    position: 0.2,
    stop_loss_price: 11.25,
    sell_timing: 'T1 收盘了结（持 1 个可卖日）',
    field_snapshot: {},
  },
};

function setup() {
  const mocked = mockFetch((url) => {
    if (url === '/api/advice/dates') return { body: { dates: ['2026-06-03'], limit: 30 } };
    if (url === '/api/advice') return { body: { trade_date: '2026-06-03', kind: null, strategy_id: null, items: [ADVICE] } };
    return undefined;
  });
  const { Wrapper } = createQueryWrapper();
  return { ...mocked, render: () => render(<Wrapper><AdvicePage /></Wrapper>) };
}

describe('AdvicePage', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('渲染建议卡片：路次 / 标的 / 买点 / 仓位 / 止损 / 门槛明细', async () => {
    setup().render();

    expect(await screen.findByText('测试一号')).toBeInTheDocument();
    expect(screen.getByText('S2')).toBeInTheDocument();
    expect(screen.getByText(/600001/)).toBeInTheDocument();
    expect(screen.getByText('06-04', { exact: false })).toBeInTheDocument();
    expect(screen.getByText('首阴形态=尾盘跳水')).toBeInTheDocument();
    expect(screen.getByText('首阴振幅≥8%')).toBeInTheDocument();
    expect(screen.getByText(/T1 收盘了结/)).toBeInTheDocument();
    expect(screen.getAllByTestId('advice-card')).toHaveLength(1);
  });

  it('日期下拉可选历史交易日并按该日请求', async () => {
    const { calls, render } = setup();
    render();

    // 等日期 option 渲染完成（原生 select 需存在对应 option 才能选中）
    const option = await screen.findByRole('option', { name: '2026-06-03' });
    expect(option).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('交易日'), { target: { value: '2026-06-03' } });

    await vi.waitFor(() => {
      expect(calls.some((call) => call.url.includes('/api/advice?date=2026-06-03'))).toBe(true);
    });
  });
});
