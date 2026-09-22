/**
 * 数据源页：健康度与能力顺序渲染，以及主备顺序保存（二次确认后）调用
 * `PUT /api/datasources/prefs`。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createQueryWrapper, findCall, mockFetch } from '@/test/queryWrapper';
import type { DatasourcesResponse } from '@/types/config';
import { DatasourcesPanel } from '../DatasourcesPanel';

const DATASOURCES: DatasourcesResponse = {
  items: [
    {
      source_id: 'hithink',
      label: '同花顺',
      kind: 'http',
      capabilities: ['limit_up_pool', 'daily_bars'],
      rate_limit_per_min: 20,
      enabled: true,
      priority: 0,
      health: { limit_up_pool: { ok: true, latency_ms: 120, detail: null } },
      last_check: '2026-09-18T01:30:00Z',
    },
    {
      source_id: 'eltdx',
      label: '通达信',
      kind: 'tcp',
      capabilities: ['daily_bars'],
      rate_limit_per_min: 60,
      enabled: false,
      priority: 1,
      health: { daily_bars: { ok: false, latency_ms: 900, detail: { error: 'timeout' } } },
      last_check: null,
    },
  ],
  capability_order: {
    limit_up_pool: ['hithink', 'eltdx'],
    daily_bars: ['eltdx'],
  },
};

function renderPanel() {
  const { Wrapper } = createQueryWrapper();
  return render(
    <Wrapper>
      <DatasourcesPanel isAdmin />
    </Wrapper>,
  );
}

describe('DatasourcesPanel', () => {
  let calls: ReturnType<typeof mockFetch>['calls'];

  beforeEach(() => {
    const mocked = mockFetch((url) => {
      if (url === '/api/datasources') return { body: DATASOURCES };
      if (url === '/api/ingest/jobs') return { body: { limit: 50, items: [] } };
      if (url === '/api/ingest/health') return { body: { since: '2026-09-11T00:00:00Z', capabilities: {} } };
      if (url === '/api/datasources/prefs') return { body: { prefs: {} } };
      return undefined;
    });
    calls = mocked.calls;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('渲染数据源健康度与声明能力', async () => {
    renderPanel();

    expect(await screen.findByText('limit_up_pool 正常 · 120ms')).toBeInTheDocument();
    expect(screen.getByText('daily_bars 失败 · 900ms')).toBeInTheDocument();
    // 标签同时出现在注册表与主备顺序编辑器中
    expect((await screen.findAllByText('同花顺')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('通达信').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: '探测全部' })).toBeInTheDocument();
  });

  it('渲染能力 → 有序数据源列表（主源在前）', async () => {
    renderPanel();

    expect(await screen.findByText('主源：同花顺')).toBeInTheDocument();
    // daily_bars 只有 eltdx，主源即通达信
    expect(screen.getByText('主源：通达信')).toBeInTheDocument();
    // 能力名同时出现在声明能力徽标与编辑器标题中
    expect((await screen.findAllByText('limit_up_pool')).length).toBeGreaterThan(1);
    expect(screen.getAllByText('daily_bars').length).toBeGreaterThan(1);
  });

  it('调整顺序并在二次确认后调用 PUT /api/datasources/prefs', async () => {
    renderPanel();

    // eltdx 在 limit_up_pool（第 2 位，可上移）与 daily_bars（第 1 位，禁用）各有一个上移按钮
    const upButtons = await screen.findAllByRole('button', { name: 'eltdx 上移' });
    const enabled = upButtons.find((button) => !button.hasAttribute('disabled'));
    expect(enabled).toBeDefined();
    fireEvent.click(enabled as HTMLElement);

    fireEvent.click(screen.getByRole('button', { name: '保存主备顺序' }));
    // 二次确认对话框
    expect(await screen.findByRole('dialog', { name: '保存能力主备顺序？' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认保存' }));

    await waitFor(() => {
      const call = findCall(calls, '/api/datasources/prefs', 'PUT');
      expect(call).toBeDefined();
      expect(call?.body).toEqual({
        prefs: { limit_up_pool: ['eltdx', 'hithink'], daily_bars: ['eltdx'] },
      });
    });
  });

  it('停用数据源前弹出二次确认', async () => {
    renderPanel();

    const disable = await screen.findByRole('button', { name: '停用' });
    fireEvent.click(disable);

    const dialog = await screen.findByRole('dialog', { name: /停用数据源 同花顺/ });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText(/退出所有能力的主备取数/)).toBeInTheDocument();
    // 取消则不发起请求
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    await waitFor(() => {
      expect(findCall(calls, '/api/datasources/hithink/disable', 'POST')).toBeUndefined();
    });
  });
});
