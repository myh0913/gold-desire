import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

// vite `base: '/gd/'` 会让 BASE_URL 带上子路径前缀，导致 API BASE 变成 '/gd/api'，
// 与测试里 mock 的 '/api/...' 精确匹配脱节。测试环境固定 API 基址为 '/api'。
(import.meta.env as Record<string, unknown>).VITE_API_BASE = '/api';

// jsdom 未实现 ResizeObserver（recharts ResponsiveContainer 依赖），补一个空实现。
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}

afterEach(() => {
  cleanup();
});
