/**
 * API 请求封装与端点契约。
 *
 * - 基础地址来自 `VITE_API_BASE`，缺省 `/api`（走 Vite 代理；后端路由自带 `/api` 前缀）。
 * - 默认 `credentials: 'include'`（HttpOnly refresh cookie 依赖此项），并在有
 *   access token 时附带 `Authorization: Bearer`。
 * - 非 2xx 抛出类型化 :class:`ApiError`。
 * - **401 统一派发 `gold:unauthorized` 事件**，由 :mod:`hooks/useAuth` 消费。
 * - 所有 GET 支持 `AbortSignal`（TanStack Query 取消）。
 */

import type {
  ApiErrorBody,
  CaptchaResponse,
  InvitationCreate,
  InvitationOut,
  LoginRequest,
  MeResponse,
  PasswordReset,
  RegisterRequest,
  RoleCreate,
  RoleOut,
  RolePagesUpdate,
  TokenResponse,
  UserCreate,
  UserOut,
  UserUpdate,
} from '@/types';
import type {
  ConfigDiffOut,
  ConfigUpdate,
  ConfigVersionOut,
  ConfigVersionsResponse,
  DatasourcePingResponse,
  DatasourcePrefsResponse,
  DatasourceStateOut,
  DatasourcesResponse,
  FactorEffectivenessResponse,
  FactorOut,
  FactorsResponse,
  IngestHealthResponse,
  IngestJobsResponse,
  IngestTriggerOut,
  IngestTriggerRequest,
  StrategiesResponse,
  StrategyOut,
  StrategyStateOut,
} from '@/types/config';
import type {
  DatesResponse,
  LadderMatrixResponse,
  LadderPage,
  MonitorResponse,
  NewsFlashPage,
  PoolsResponse,
  PoolResponse,
  CycleResponse,
  SentimentHistoryResponse,
  SentimentResponse,
  ThemeStocksResponse,
  ThemesResponse,
} from '@/types/market';
import type {
  AdviceLatestResponse,
  AdviceResponse,
  BacktestRunOut,
  BacktestRunsResponse,
  BacktestRunRequest,
  DragonPoolResponse,
} from '@/types/report';
import type { ReviewResponse } from '@/types/review';
import { APP_BASE } from './appBase';
import { clearAccessToken, getAccessToken } from './tokenStore';

/** 全局未授权事件名。 */
export const UNAUTHORIZED_EVENT = 'gold:unauthorized';

/** 类型化 API 错误。 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: unknown;

  constructor(status: number, body: ApiErrorBody | null) {
    super(body?.error?.message ?? `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = body?.error?.code ?? 'unknown';
    this.detail = body?.error?.detail ?? null;
  }
}

// 显式配置优先；否则补上应用挂载前缀（生产 '/gd'，本地 dev ''），
// 使子路径反代部署下请求命中 /gd/api/*，而不是裸根路径 /api/*。
const BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? `${APP_BASE}/api`;

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  /** 附加查询参数（作用于 URL） */
  params?: Record<string, string | number | undefined | null>;
  /** 请求体 JSON，自动序列化 */
  json?: unknown;
  /** 携带 Cookie 凭证（默认 true） */
  credentials?: boolean;
  /** 取消信号 */
  signal?: AbortSignal;
}

function buildUrl(path: string, params?: RequestOptions['params']): string {
  const clean = path.startsWith('/') ? path : `/${path}`;
  const url = `${BASE}${clean}`;
  if (!params) return url;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      search.set(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `${url}?${qs}` : url;
}

function dispatchUnauthorized(): void {
  clearAccessToken();
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT));
  }
}

/** 通用 JSON 请求入口：非 2xx 抛出 :class:`ApiError`。 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers({ Accept: 'application/json' });
  const token = getAccessToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);

  let body: BodyInit | undefined;
  if (options.json !== undefined) {
    headers.set('Content-Type', 'application/json');
    body = JSON.stringify(options.json);
  }

  const resp = await fetch(buildUrl(path, options.params), {
    method: options.method ?? (options.json !== undefined ? 'POST' : 'GET'),
    headers,
    body,
    credentials: options.credentials === false ? 'same-origin' : 'include',
    signal: options.signal,
  });

  if (!resp.ok) {
    let parsed: ApiErrorBody | null = null;
    try {
      parsed = (await resp.json()) as ApiErrorBody;
    } catch {
      parsed = null;
    }
    if (resp.status === 401) dispatchUnauthorized();
    throw new ApiError(resp.status, parsed);
  }

  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

/** 后端 API 基础地址。 */
export function getApiBaseUrl(): string {
  return BASE;
}

/** WebSocket 地址：优先 `VITE_WS_BASE`，否则按当前页面协议/主机推导 `<base>/ws`。 */
export function getWsUrl(): string {
  const explicit = import.meta.env.VITE_WS_BASE as string | undefined;
  if (explicit) return explicit;
  if (typeof window === 'undefined') return `${APP_BASE}/ws`;
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}${APP_BASE}/ws`;
}

const seg = (value: string) => encodeURIComponent(value);

/** 类型化端点集合：认证 + 管理员（用户/角色/邀请码）。 */
export const api = {
  auth: {
    captcha: (signal?: AbortSignal) => request<CaptchaResponse>('/captcha', { signal }),
    login: (body: LoginRequest) => request<TokenResponse>('/auth/login', { json: body }),
    register: (body: RegisterRequest) => request<UserOut>('/auth/register', { json: body }),
    refresh: () => request<TokenResponse>('/auth/refresh', { method: 'POST' }),
    logout: () => request<void>('/auth/logout', { method: 'POST' }),
    me: (signal?: AbortSignal) => request<MeResponse>('/auth/me', { signal }),
  },
  admin: {
    users: {
      list: (signal?: AbortSignal) => request<UserOut[]>('/admin/users', { signal }),
      create: (body: UserCreate) => request<UserOut>('/admin/users', { json: body }),
      update: (username: string, body: UserUpdate) =>
        request<UserOut>(`/admin/users/${seg(username)}`, { method: 'PATCH', json: body }),
      resetPassword: (username: string, body: PasswordReset) =>
        request<UserOut>(`/admin/users/${seg(username)}/password`, { json: body }),
      remove: (username: string) =>
        request<void>(`/admin/users/${seg(username)}`, { method: 'DELETE' }),
    },
    roles: {
      list: (signal?: AbortSignal) => request<RoleOut[]>('/admin/roles', { signal }),
      get: (name: string, signal?: AbortSignal) =>
        request<RoleOut>(`/admin/roles/${seg(name)}`, { signal }),
      create: (body: RoleCreate) => request<RoleOut>('/admin/roles', { json: body }),
      setPages: (name: string, body: RolePagesUpdate) =>
        request<RoleOut>(`/admin/roles/${seg(name)}/pages`, { method: 'PUT', json: body }),
      resetPages: (name: string) =>
        request<RoleOut>(`/admin/roles/${seg(name)}/pages/reset`, { method: 'POST' }),
      remove: (name: string) => request<void>(`/admin/roles/${seg(name)}`, { method: 'DELETE' }),
    },
    invitations: {
      list: (signal?: AbortSignal) =>
        request<InvitationOut[]>('/admin/invitations', { signal }),
      create: (body: InvitationCreate) =>
        request<InvitationOut>('/admin/invitations', { json: body }),
      revoke: (code: string) =>
        request<InvitationOut>(`/admin/invitations/${seg(code)}`, { method: 'DELETE' }),
    },
  },
};

// ============================================================ 量化配置 / 运维
// 以下端点均为**追加**（不改变上方既有导出），供 Task 15 的量化配置页与总览页使用。

/** 量化配置端点：策略 / 因子 / 数据源 / 采集。读接口需 `quantconfig` 页面权限，写接口 admin-only。 */
export const configApi = {
  strategies: {
    list: (signal?: AbortSignal) =>
      request<StrategiesResponse>('/strategies', { signal }),
    get: (strategyId: string, signal?: AbortSignal) =>
      request<StrategyOut>(`/strategies/${seg(strategyId)}`, { signal }),
    save: (strategyId: string, body: ConfigUpdate) =>
      request<ConfigVersionOut>(`/strategies/${seg(strategyId)}/config`, {
        method: 'PUT',
        json: body,
      }),
    versions: (strategyId: string, signal?: AbortSignal) =>
      request<ConfigVersionsResponse>(`/strategies/${seg(strategyId)}/versions`, { signal }),
    diff: (strategyId: string, from: number, to: number, signal?: AbortSignal) =>
      request<ConfigDiffOut>(`/strategies/${seg(strategyId)}/versions/diff`, {
        params: { from, to },
        signal,
      }),
    rollback: (strategyId: string, version: number) =>
      request<ConfigVersionOut>(`/strategies/${seg(strategyId)}/rollback/${version}`, {
        method: 'POST',
      }),
    enable: (strategyId: string) =>
      request<StrategyStateOut>(`/strategies/${seg(strategyId)}/enable`, { method: 'POST' }),
    disable: (strategyId: string) =>
      request<StrategyStateOut>(`/strategies/${seg(strategyId)}/disable`, { method: 'POST' }),
  },
  factors: {
    list: (signal?: AbortSignal) => request<FactorsResponse>('/factors', { signal }),
    get: (factorId: string, signal?: AbortSignal) =>
      request<FactorOut>(`/factors/${seg(factorId)}`, { signal }),
    save: (factorId: string, body: ConfigUpdate) =>
      request<ConfigVersionOut>(`/factors/${seg(factorId)}/config`, {
        method: 'PUT',
        json: body,
      }),
    versions: (factorId: string, signal?: AbortSignal) =>
      request<ConfigVersionsResponse>(`/factors/${seg(factorId)}/versions`, { signal }),
    diff: (factorId: string, from: number, to: number, signal?: AbortSignal) =>
      request<ConfigDiffOut>(`/factors/${seg(factorId)}/versions/diff`, {
        params: { from, to },
        signal,
      }),
    rollback: (factorId: string, version: number) =>
      request<ConfigVersionOut>(`/factors/${seg(factorId)}/rollback/${version}`, {
        method: 'POST',
      }),
    effectiveness: (factorId: string, signal?: AbortSignal) =>
      request<FactorEffectivenessResponse>(`/factors/${seg(factorId)}/effectiveness`, { signal }),
  },
  datasources: {
    list: (signal?: AbortSignal) => request<DatasourcesResponse>('/datasources', { signal }),
    savePrefs: (prefs: Record<string, string[]>) =>
      request<DatasourcePrefsResponse>('/datasources/prefs', { method: 'PUT', json: { prefs } }),
    ping: (sources: string[]) =>
      request<DatasourcePingResponse>('/datasources/ping', { json: { sources } }),
    enable: (sourceId: string) =>
      request<DatasourceStateOut>(`/datasources/${seg(sourceId)}/enable`, { method: 'POST' }),
    disable: (sourceId: string) =>
      request<DatasourceStateOut>(`/datasources/${seg(sourceId)}/disable`, { method: 'POST' }),
  },
  ingest: {
    jobs: (signal?: AbortSignal) => request<IngestJobsResponse>('/ingest/jobs', { signal }),
    health: (signal?: AbortSignal) => request<IngestHealthResponse>('/ingest/health', { signal }),
    trigger: (body: IngestTriggerRequest) =>
      request<IngestTriggerOut>('/ingest/trigger', { json: body }),
  },
};

/** 行情读端点（总览 + Phase 2 各页）。 */
export const marketApi = {
  sentiment: (signal?: AbortSignal) => request<SentimentResponse>('/sentiment', { signal }),
  /** 情绪周期判定（派生数据；缺省取库中最新）。 */
  cycle: (params: { date?: string }, signal?: AbortSignal) =>
    request<CycleResponse>('/cycle', { params, signal }),
  sentimentHistory: (days: number, signal?: AbortSignal) =>
    request<SentimentHistoryResponse>('/sentiment/history', { params: { days }, signal }),
  pools: (params: { date?: string }, signal?: AbortSignal) =>
    request<PoolsResponse>('/pools', { params, signal }),
  pool: (
    poolType: string,
    params: { date?: string; min_continue_days?: number },
    signal?: AbortSignal,
  ) =>
    request<PoolResponse>(`/pools/${encodeURIComponent(poolType)}`, {
      params,
      signal,
    }),
  ladder: (
    params: {
      start: string;
      end: string;
      min_continue_days?: number;
      page?: number;
      page_size?: number;
    },
    signal?: AbortSignal,
  ) => request<LadderPage>('/ladder', { params, signal }),
  ladderMatrix: (
    params: {
      start?: string;
      end?: string;
      min_continue_days?: number;
      limit_days?: number;
    },
    signal?: AbortSignal,
  ) => request<LadderMatrixResponse>('/ladder/matrix', { params, signal }),
  ladderDates: (signal?: AbortSignal) =>
    request<DatesResponse>('/ladder/dates', { signal }),
  newsflash: (
    params: { level?: string; keyword?: string; page?: number; page_size?: number },
    signal?: AbortSignal,
  ) => request<NewsFlashPage>('/newsflash', { params, signal }),
  themes: (params: { date?: string }, signal?: AbortSignal) =>
    request<ThemesResponse>('/themes', { params, signal }),
  themeDates: (signal?: AbortSignal) =>
    request<DatesResponse>('/themes/dates', { signal }),
  themeStocks: (onDate: string, themeName: string, signal?: AbortSignal) =>
    request<ThemeStocksResponse>(
      `/themes/${encodeURIComponent(onDate)}/${encodeURIComponent(themeName)}/stocks`,
      { signal },
    ),
  monitor: (params: { date?: string; kind?: string }, signal?: AbortSignal) =>
    request<MonitorResponse>('/monitor', { params, signal }),
};

/** 报告读端点（建议 / 复盘 / 回测）。 */
export const reportApi = {
  advice: (
    params: { date?: string; kind?: string; strategy_id?: string },
    signal?: AbortSignal,
  ) => request<AdviceResponse>('/advice', { params, signal }),
  adviceLatest: (params: { date?: string }, signal?: AbortSignal) =>
    request<AdviceLatestResponse>('/advice/latest', { params, signal }),
  adviceDates: (signal?: AbortSignal) =>
    request<DatesResponse>('/advice/dates', { signal }),
  dragonPool: (params: { date?: string }, signal?: AbortSignal) =>
    request<DragonPoolResponse>('/dragon/pool', { params, signal }),
  dragonPoolDates: (signal?: AbortSignal) =>
    request<DatesResponse>('/dragon/pool/dates', { signal }),
  review: (params: { date?: string }, signal?: AbortSignal) =>
    request<ReviewResponse>('/review', { params, signal }),
  reviewDates: (signal?: AbortSignal) =>
    request<DatesResponse>('/review/dates', { signal }),
  backtestRuns: (signal?: AbortSignal) =>
    request<BacktestRunsResponse>('/backtest/runs', { signal }),
  backtestRun: (runId: string, signal?: AbortSignal) =>
    request<BacktestRunOut>(
      `/backtest/runs/${encodeURIComponent(runId)}`,
      { signal },
    ),
  triggerBacktest: (body: BacktestRunRequest) =>
    request<BacktestRunOut>('/backtest/run', { json: body }),
};
