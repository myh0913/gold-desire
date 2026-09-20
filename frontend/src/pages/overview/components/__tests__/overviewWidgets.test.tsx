/**
 * 总览页派生组件：温度计（夹取 / 连板高度 / 溢价着色）、统计卡（五卡 + 炸板率）、
 * 历史行映射（溢价百分化 / 日期截断）。
 */

import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SentimentGauge } from '../SentimentGauge';
import { StatCards } from '../StatCards';
import { toHistoryRows } from '../../lib/historyRows';
import type { SentimentOut } from '@/types/market';

const FIXTURE: SentimentOut = {
  trade_date: '2026-09-18',
  temperature: 72,
  stage: '修复',
  limit_up_count: 68,
  limit_down_count: 3,
  broken_board_count: 31,
  broken_rate: 0.45,
  up_count: 2100,
  down_count: 1500,
  max_continue_days: 5,
  premium_rate: 0.012,
};

describe('SentimentGauge', () => {
  it('渲染温度、阶段与连板高度 / 涨停溢价脚注', () => {
    render(
      <SentimentGauge
        temperature={FIXTURE.temperature}
        stage={FIXTURE.stage}
        maxContinueDays={FIXTURE.max_continue_days}
        premiumRate={FIXTURE.premium_rate}
      />,
    );
    const svg = screen.getByRole('img', { name: '情绪温度 72.0 分，阶段 修复' });
    expect(svg).toBeInTheDocument();
    expect(screen.getByTestId('gauge-footnote')).toHaveTextContent('连板高度 5 板');
    expect(screen.getByTestId('gauge-footnote')).toHaveTextContent('涨停溢价 +1.20%');
  });

  it('温度越界被夹取到 0~100，负溢价渲染为负数', () => {
    const { rerender } = render(<SentimentGauge temperature={130} stage="加速/高潮" />);
    expect(screen.getByRole('img', { name: '情绪温度 100.0 分，阶段 加速/高潮' }));

    rerender(
      <SentimentGauge temperature={-9} stage="冰点" maxContinueDays={2} premiumRate={-0.034} />,
    );
    expect(screen.getByRole('img', { name: '情绪温度 0.0 分，阶段 冰点' }));
    expect(screen.getByTestId('gauge-footnote')).toHaveTextContent('连板高度 2 板');
    expect(screen.getByTestId('gauge-footnote')).toHaveTextContent('涨停溢价 -3.40%');
  });

  it('缺省连板高度 / 溢价时以 -- 兜底', () => {
    render(<SentimentGauge temperature={50} stage="分歧" />);
    expect(screen.getByTestId('gauge-footnote')).toHaveTextContent('连板高度 --');
    expect(screen.getByTestId('gauge-footnote')).toHaveTextContent('涨停溢价 --');
  });
});

describe('StatCards', () => {
  it('由 fixture 推导五张统计卡（含炸板率提示）', () => {
    render(<StatCards sentiment={FIXTURE} />);
    for (const label of ['涨停', '跌停', '炸板', '上涨', '下跌']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText('68')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('31')).toBeInTheDocument();
    expect(screen.getByText('炸板率 45%')).toBeInTheDocument();
    expect(screen.getByText('2,100')).toBeInTheDocument();
    expect(screen.getByText('1,500')).toBeInTheDocument();
  });
});

describe('toHistoryRows', () => {
  it('溢价百分化、温度保留一位小数、日期截断为 MM-DD 且保留全量值', () => {
    const [row] = toHistoryRows([FIXTURE]);
    expect(row).toMatchObject({
      date: '09-18',
      fullDate: '2026-09-18',
      temperature: 72,
      premiumPct: 1.2,
      limitUp: 68,
      limitDown: 3,
      broken: 31,
      up: 2100,
      down: 1500,
    });
  });
});
