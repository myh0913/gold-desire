/**
 * useChannelRefresh 冒烟：订阅频道 → 收到该频道薄事件即失效查询键前缀；
 * 其他频道事件不触发；卸载后退订。
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { WsClient } from '@/lib/ws';
import { useChannelRefresh } from '@/hooks/useChannelRefresh';

vi.mock('@/lib/ws', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/ws')>();
  return {
    ...actual,
    getWsClient: () => fakeClient,
  };
});

const listeners = new Set<(msg: { type: string; data?: unknown }) => void>();
const fakeClient = {
  connect: vi.fn(),
  subscribe: vi.fn(),
  unsubscribe: vi.fn(),
  onMessage: vi.fn((listener: (msg: { type: string; data?: unknown }) => void) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }),
} as unknown as WsClient;

function emit(type: string): void {
  for (const listener of [...listeners]) listener({ type, data: {} });
}

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { invalidate, wrapper };
}

describe('useChannelRefresh', () => {
  afterEach(() => {
    vi.clearAllMocks();
    listeners.clear();
  });

  it('收到所订阅频道事件时失效查询键前缀', () => {
    const { invalidate, wrapper } = setup();
    renderHook(() => useChannelRefresh(['pool'], ['market', 'pools']), { wrapper });

    expect(fakeClient.subscribe).toHaveBeenCalledWith(['pool']);
    emit('pool');
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['market', 'pools'] });
  });

  it('其他频道事件不触发失效', () => {
    const { invalidate, wrapper } = setup();
    renderHook(() => useChannelRefresh(['pool'], ['market', 'pools']), { wrapper });

    emit('sentiment');
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('卸载后退订频道并移除监听', () => {
    const { wrapper } = setup();
    const { unmount } = renderHook(() => useChannelRefresh(['pool'], ['market', 'pools']), {
      wrapper,
    });
    unmount();

    expect(fakeClient.unsubscribe).toHaveBeenCalledWith(['pool']);
    emit('pool');
    expect(listeners.size).toBe(0);
  });
});
