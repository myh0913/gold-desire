/**
 * 复盘页冒烟（mock fetch）：情绪面板 / 池型统计 / 天梯头部 / 建议回溯三态与汇总。
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { createQueryWrapper, mockFetch } from '@/test/queryWrapper';
import ReviewPage from '@/pages/review/ReviewPage';

const REVIEW = {
  trade_date: '2026-06-03',
  sentiment: {
    trade_date: '2026-06-03',
    temperature: 62.5,
    stage: '加速/高潮',
    limit_up_count: 42,
    limit_down_count: 3,
    broken_board_count: 6,
    broken_rate: 0.125,
    up_count: 2600,
    down_count: 1400,
    max_continue_days: 5,
    premium_rate: 0.025,
  },
  prev_trade_date: '2026-06-02',
  prev_temperature: 58.0,
  temperature_delta: 4.5,
  pool_counts: { limit_up: 2, limit_up_broken: 1 },
  top_ladder: [
    {
      code: '600001',
      name: '测试一号',
      continue_days: 3,
      limit_up_time: '09:31',
      seal_amount_yuan: 1000000,
      turnover_rate: 0.0812,
    },
  ],
  advices: [
    {
      code: '600001',
      name: '测试一号',
      path_id: 'S2',
      path_label: '高位跳水型',
      buy_day: '2026-06-04',
      buy_price: 11.6,
      position: 0.2,
      stop_loss_price: 11.25,
      sell_timing: 'T1 收盘了结',
      bonus_score: 1,
      status: 'closed',
      return_pct: 0.0345,
      sell_date: '2026-06-05',
      sell_price: 12.0,
      ran_at: null,
    },
    {
      code: '600002',
      name: '测试二号',
      path_id: 'S4',
      path_label: '缩量反转型',
      buy_day: '2026-06-04',
      buy_price: 11.6,
      position: 0.25,
      stop_loss_price: 11.8,
      sell_timing: null,
      bonus_score: 0,
      status: 'stopped',
      return_pct: 0.0172,
      sell_date: '2026-06-05',
      sell_price: 11.8,
      ran_at: null,
    },
    {
      code: '600003',
      name: '测试三号',
      path_id: 'S2',
      path_label: '高位跳水型',
      buy_day: '2026-06-05',
      buy_price: 14.6,
      position: 0.2,
      stop_loss_price: 14.16,
      sell_timing: null,
      bonus_score: 0,
      status: 'pending',
      return_pct: null,
      sell_date: null,
      sell_price: null,
      ran_at: null,
    },
  ],
  advice_stats: {
    total: 3,
    settled: 2,
    pending: 1,
    win_count: 2,
    win_rate: 1.0,
    avg_return_pct: 0.02586,
  },
  meta: {},
};

function setup() {
  mockFetch((url) => {
    if (url === '/api/review/dates') return { body: { dates: ['2026-06-03'], limit: 30 } };
    if (url === '/api/review') return { body: REVIEW };
    return undefined;
  });
  const { Wrapper } = createQueryWrapper();
  return { render: () => render(<Wrapper><ReviewPage /></Wrapper>) };
}

describe('ReviewPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('渲染情绪面板（温度 / 阶段 / 环比）与池型统计', async () => {
    setup().render();

    expect(await screen.findByText('62.5')).toBeInTheDocument();
    expect(screen.getByText('加速/高潮')).toBeInTheDocument();
    expect(screen.getByText(/较前日 \+4\.5/)).toBeInTheDocument();
    expect(screen.getByText('涨停池 2')).toBeInTheDocument();
    expect(screen.getByText('炸板池 1')).toBeInTheDocument();
  });

  it('渲染天梯头部与建议回溯（三态 + 汇总）', async () => {
    setup().render();

    expect(await screen.findByText('3 板')).toBeInTheDocument();
    expect(screen.getAllByText('600001').length).toBeGreaterThanOrEqual(2); // 天梯 + 建议回溯

    expect(screen.getAllByText('已了结').length).toBeGreaterThanOrEqual(2); // 统计标签 + 状态徽标
    expect(screen.getByText('止损离场')).toBeInTheDocument();
    expect(screen.getAllByText('待评估').length).toBeGreaterThanOrEqual(2); // 统计标签 + 状态徽标
    expect(screen.getByText('+3.45%')).toBeInTheDocument();
    expect(screen.getByText('100.0%')).toBeInTheDocument(); // 胜率
  });
});
