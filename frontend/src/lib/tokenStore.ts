/**
 * access token 持有者。
 *
 * 后端 access token 走响应体（短 TTL），refresh token 走 HttpOnly Cookie，
 * 因此前端必须自行保存 access token 并在请求头携带。仅存于内存 + sessionStorage
 * （关闭标签页即失效），避免落到 localStorage 造成长期暴露。
 */

const STORAGE_KEY = 'gd.access_token';

let cached: string | null = null;
let loaded = false;

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

/** 读取当前 access token（首次调用时从 sessionStorage 懒加载）。 */
export function getAccessToken(): string | null {
  if (!loaded) {
    loaded = true;
    cached = storage()?.getItem(STORAGE_KEY) ?? null;
  }
  return cached;
}

/** 写入或清除 access token。 */
export function setAccessToken(token: string | null): void {
  cached = token;
  loaded = true;
  const store = storage();
  if (!store) return;
  if (token) store.setItem(STORAGE_KEY, token);
  else store.removeItem(STORAGE_KEY);
}

/** 清除 access token（登出 / 401）。 */
export function clearAccessToken(): void {
  setAccessToken(null);
}
