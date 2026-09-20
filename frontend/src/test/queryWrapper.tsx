/**
 * 测试辅助：TanStack Query 包裹与 fetch 桩。
 */

import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { vi } from 'vitest';

/** 构造一个不重试、无缓存的 QueryClient 与对应 Provider 包裹组件。 */
export function createQueryWrapper() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0, refetchOnWindowFocus: false },
      mutations: { retry: 0 },
    },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  }
  return { client, Wrapper };
}

export interface MockReply {
  status?: number;
  body?: unknown;
}

export type MockHandler = (url: string, init: RequestInit) => MockReply | undefined;

export interface FetchCall {
  url: string;
  method: string;
  body: unknown;
}

function parseBody(raw: unknown): unknown {
  if (typeof raw !== 'string' || raw === '') return undefined;
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

/**
 * 安装 fetch 桩：命中 `handler` 返回其响应，未命中返回 404（便于发现漏 mock）。
 * 返回的 `calls` 记录全部请求，供断言端点与请求体。
 */
export function mockFetch(handler: MockHandler) {
  const calls: FetchCall[] = [];
  const impl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const options = init ?? {};
    calls.push({
      url,
      method: options.method ?? 'GET',
      body: parseBody(options.body),
    });
    const reply = handler(url, options);
    const status = reply?.status ?? (reply ? 200 : 404);
    const body =
      reply !== undefined
        ? reply.body ?? {}
        : { error: { code: 'not_found', message: `未 mock 的请求：${url}` } };
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    } as unknown as Response;
  });
  vi.stubGlobal('fetch', impl);
  return { calls, impl };
}

/** 在已记录的请求中查找指定 URL + 方法。 */
export function findCall(calls: readonly FetchCall[], url: string, method = 'GET'): FetchCall | undefined {
  return calls.find((call) => call.url === url && call.method === method);
}
