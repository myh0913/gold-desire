/**
 * 总览页：统计卡 + 温度计渲染，以及 `stale: true` 时的陈旧数据横幅。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { FakeWebSocket } from '@/test/wsMock';
import { mockFetch } from '@/test/queryWrapper';
import { createQueryWrapper } from '@/test/queryWrapper';
import OverviewPage from '../OverviewPage';

const SENTIMENT = {
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

const HISTORY = {
  days: 20,
  items: [
    { ...SENTIMENT, trade_date: '2026-09-17', temperature: 60 },
    { ...SENTIMENT, trade_date: '2026-09-18', temperature: 72 },
  ],
};

function renderOverview() {
  const { Wrapper } = createQueryWrapper();
  return render(
    <Wrapper>
      <OverviewPage />
    </Wrapper>,
  );
}

describe('OverviewPage', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    mockFetch((url) => {
      if (url.startsWith('/api/sentiment/history')) {
        return { body: { stale: false, data_date: '2026-09-18', ...HISTORY } };
      }
      if (url.startsWith('/api/sentiment')) {
        return { body: { stale: false, data_date: '2026-09-18', trade_date: '2026-09-18', item: SENTIMENT } };
      }
      return undefined;
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('渲染温度计（温度 + 阶段标签 + 连板高度 / 溢价脚注）与统计卡', async () => {
    renderOverview();

    expect(
      await screen.findByRole('img', { name: /情绪温度 72\.0 分，阶段 修复/ }),
    ).toBeInTheDocument();
    expect(screen.getByTestId('gauge-footnote')).toHaveTextContent('连板高度 5 板');
    expect(screen.getByTestId('gauge-footnote')).toHaveTextContent('涨停溢价 +1.20%');

    expect(screen.getByText('涨停')).toBeInTheDocument();
    expect(screen.getByText('68')).toBeInTheDocument();
    expect(screen.getByText('跌停')).toBeInTheDocument();
    expect(screen.getByText('炸板')).toBeInTheDocument();
    expect(screen.getByText('炸板率 45%')).toBeInTheDocument();
    expect(screen.getByText('2,100')).toBeInTheDocument();
    expect(screen.getByText('1,500')).toBeInTheDocument();

    expect(screen.getByText('观察要点')).toBeInTheDocument();
  });

  it('渲染三张情绪走势图（双轴 / 涨跌停炸板 / 上涨下跌）', async () => {
    renderOverview();

    expect(await screen.findByText('情绪温度 · 涨停溢价')).toBeInTheDocument();
    expect(screen.getByText('涨跌停 · 炸板')).toBeInTheDocument();
    expect(screen.getByText('上涨 · 下跌家数')).toBeInTheDocument();
    expect(screen.getByText('近 20 个交易日情绪走势')).toBeInTheDocument();
  });

  it('stale: true 时展示陈旧数据横幅', async () => {
    mockFetch((url) => {
      if (url.startsWith('/api/sentiment/history')) {
        return { body: { stale: true, data_date: '2026-09-18', ...HISTORY } };
      }
      return {
        body: { stale: true, data_date: '2026-09-18', trade_date: '2026-09-18', item: SENTIMENT },
      };
    });

    renderOverview();

    expect(await screen.findByText(/上游数据可能未及时更新/)).toBeInTheDocument();
    expect(screen.getByText('数据可能不是最新')).toBeInTheDocument();
  });
});
