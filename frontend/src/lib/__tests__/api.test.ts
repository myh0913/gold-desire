import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, UNAUTHORIZED_EVENT, request } from '../api';
import { getAccessToken, setAccessToken } from '../tokenStore';

/** 最小 fetch Response 替身（避免依赖运行环境的 Response 实现）。 */
function fakeResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe('api.request', () => {
  beforeEach(() => {
    setAccessToken(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('401 时派发 gold:unauthorized 并抛出类型化 ApiError', async () => {
    const listener = vi.fn();
    window.addEventListener(UNAUTHORIZED_EVENT, listener);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        fakeResponse(401, { error: { code: 'unauthorized', message: '未登录或缺少凭证' } }),
      ),
    );

    await expect(request('/auth/me')).rejects.toMatchObject({
      name: 'ApiError',
      status: 401,
      code: 'unauthorized',
      message: '未登录或缺少凭证',
    });
    expect(listener).toHaveBeenCalledTimes(1);

    window.removeEventListener(UNAUTHORIZED_EVENT, listener);
  });

  it('401 会清空本地 access token', async () => {
    setAccessToken('stale-token');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fakeResponse(401, { error: { code: 'unauthorized', message: 'x' } })));

    await expect(request('/auth/me')).rejects.toBeInstanceOf(ApiError);
    expect(getAccessToken()).toBeNull();
  });

  it('非 401 错误不派发未授权事件', async () => {
    const listener = vi.fn();
    window.addEventListener(UNAUTHORIZED_EVENT, listener);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fakeResponse(403, { error: { code: 'page_forbidden', message: '无权访问' } })));

    await expect(request('/admin/users')).rejects.toMatchObject({ status: 403 });
    expect(listener).not.toHaveBeenCalled();

    window.removeEventListener(UNAUTHORIZED_EVENT, listener);
  });

  it('携带 credentials 与 Authorization 头', async () => {
    setAccessToken('token-abc');
    const fetchMock = vi.fn().mockResolvedValue(fakeResponse(200, { ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    await request('/auth/me');

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.credentials).toBe('include');
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer token-abc');
  });

  it('透传 AbortSignal', async () => {
    const fetchMock = vi.fn().mockResolvedValue(fakeResponse(200, {}));
    vi.stubGlobal('fetch', fetchMock);
    const controller = new AbortController();

    await request('/health', { signal: controller.signal });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.signal).toBe(controller.signal);
  });

  it('204 返回 undefined', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fakeResponse(204, null)));
    await expect(request('/auth/logout', { method: 'POST' })).resolves.toBeUndefined();
  });
});
