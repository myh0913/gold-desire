/**
 * 策略页冒烟（mock fetch）：schema 表单渲染、percent 提交小数口径、
 * min/max 校验阻断提交、保存启用二次确认、门控矩阵摘要。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createQueryWrapper, findCall, mockFetch } from '@/test/queryWrapper';
import type { StrategiesResponse } from '@/types/config';
import { StrategiesPanel } from '../StrategiesPanel';

const STRATEGIES: StrategiesResponse = {
  items: [
    {
      strategy_id: 'dragon',
      label: '龙回头',
      version: '1.2.0',
      description: '首阴两路',
      enabled: true,
      phases: ['pool', 'intraday'],
      gate_matrix: {
        hot: { allowed: true, position_factor: 1.0 },
        ice: { allowed: false, position_factor: 0.0 },
      },
      params_schema: {
        params: [
          { key: 'base_position', label: '单路基础仓位', type: 'percent', default: 0.2, min: 0, max: 1 },
          { key: 'hold_days', label: '持有可卖日数', type: 'int', default: 1, min: 1, max: 3 },
          { key: 'scale_by_bonus', label: '按加分项加码', type: 'bool', default: true },
        ],
      },
      active_version: null,
      active_params: {},
      versions: [],
    },
  ],
};

function renderPanel() {
  const { Wrapper } = createQueryWrapper();
  return render(
    <Wrapper>
      <StrategiesPanel isAdmin />
    </Wrapper>,
  );
}

describe('StrategiesPanel', () => {
  let calls: ReturnType<typeof mockFetch>['calls'];

  beforeEach(() => {
    const mocked = mockFetch((url, init) => {
      if (url === '/api/strategies') return { body: STRATEGIES };
      if (url === '/api/strategies/dragon/versions') {
        return { body: { owner_id: 'dragon', items: [] } };
      }
      if (url === '/api/strategies/dragon/config' && init.method === 'PUT') {
        return { body: { owner_id: 'dragon', version: 2, status: 'active', params: {}, note: null, created_by: 'admin', created_at: '2026-09-19T01:00:00Z' } };
      }
      return undefined;
    });
    calls = mocked.calls;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('渲染策略卡片（含门控摘要）与门控矩阵明细', async () => {
    renderPanel();

    expect(await screen.findByText('门控 1/2 允许')).toBeInTheDocument();
    expect(screen.getByText('情绪周期门控矩阵')).toBeInTheDocument();
    expect(screen.getByText('hot')).toBeInTheDocument();
    expect(screen.getByText('ice')).toBeInTheDocument();
    expect(screen.getByText('禁止')).toBeInTheDocument();
  });

  it('percent 以百分数编辑、确认后以小数提交，整包带默认值', async () => {
    renderPanel();

    const input = await screen.findByLabelText('单路基础仓位');
    expect(input).toHaveValue(20);

    // 初始无改动：保存禁用
    const save = screen.getByRole('button', { name: '保存并启用' });
    expect(save).toBeDisabled();

    fireEvent.change(input, { target: { value: '8' } });
    await waitFor(() => expect(save).toBeEnabled());

    fireEvent.click(save);
    const dialog = await screen.findByRole('dialog', { name: /保存并启用 龙回头/ });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText(/下一轮采集即按新参数运行/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '确认保存并启用' }));
    await waitFor(() => {
      const call = findCall(calls, '/api/strategies/dragon/config', 'PUT');
      expect(call).toBeDefined();
      // percent 显示 8 → 提交 0.08；未改动的参数以 schema 默认值整包提交
      expect(call?.body).toEqual({
        params: { base_position: 0.08, hold_days: 1, scale_by_bonus: true },
        note: null,
      });
    });
  });

  it('min/max 校验未通过时阻断提交', async () => {
    renderPanel();

    const input = await screen.findByLabelText('单路基础仓位');
    fireEvent.change(input, { target: { value: '150' } });

    expect(await screen.findByText('单路基础仓位 不得大于 100%')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存并启用' })).toBeDisabled();
    expect(findCall(calls, '/api/strategies/dragon/config', 'PUT')).toBeUndefined();
  });
});
