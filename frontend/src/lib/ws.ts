/**
 * 框架无关的 WebSocket 客户端。
 *
 * 设计要点（对齐原项目 `quant-web/src/lib/ws.ts` 中已验证的部分，并补齐可测性）：
 *
 * - **指数退避重连**：`min(base * 2^n, max)`，连接成功即归零；主动 `disconnect` 不重连。
 * - **心跳 ping**：定时上行 `ping` 保活。
 * - **半开检测**：超过 `staleTimeoutMs` 未收到任何下行消息即判定死连接，主动断开触发重连。
 * - **监听器隔离**：任一监听器抛错不影响其他监听器。
 * - **订阅状态自持**：`subscribe` 的频道集合由客户端记住，重连成功后自动重订阅。
 * - **status 可观察**：`onStatus` 订阅状态变化，订阅时立即回放当前值。
 * - **可注入时钟与 socket 工厂**：单元测试可确定性驱动，不依赖真实计时器。
 */

import type { WsInbound, WsOutbound, WsStatus } from '@/types';
import { getWsUrl } from './api';
import { getAccessToken } from './tokenStore';

/** 可注入时钟，便于测试确定性驱动。 */
export interface WsClock {
  now(): number;
  setTimeout(fn: () => void, ms: number): number;
  clearTimeout(handle: number): void;
  setInterval(fn: () => void, ms: number): number;
  clearInterval(handle: number): void;
}

/** 默认时钟（浏览器环境）。 */
export const realClock: WsClock = {
  now: () => Date.now(),
  setTimeout: (fn, ms) => window.setTimeout(fn, ms),
  clearTimeout: (handle) => window.clearTimeout(handle),
  setInterval: (fn, ms) => window.setInterval(fn, ms),
  clearInterval: (handle) => window.clearInterval(handle),
};

export interface WsClientOptions {
  url: string;
  /** socket 构造器，测试可注入 mock */
  socketFactory?: (url: string) => WebSocket;
  clock?: WsClock;
  /** 心跳间隔（默认 30s） */
  pingIntervalMs?: number;
  /** 无消息判定半开的阈值（默认 60s） */
  staleTimeoutMs?: number;
  /** 重连退避基数（默认 1s） */
  baseBackoffMs?: number;
  /** 重连退避上限（默认 30s） */
  maxBackoffMs?: number;
  /** 日志开关（默认关闭） */
  debug?: boolean;
}

const DEFAULTS = {
  pingIntervalMs: 30_000,
  staleTimeoutMs: 60_000,
  baseBackoffMs: 1_000,
  maxBackoffMs: 30_000,
};

export class WsClient {
  private readonly url: string;
  private readonly socketFactory: (url: string) => WebSocket;
  private readonly clock: WsClock;
  private readonly pingIntervalMs: number;
  private readonly staleTimeoutMs: number;
  private readonly baseBackoffMs: number;
  private readonly maxBackoffMs: number;
  private readonly debug: boolean;

  private socket: WebSocket | null = null;
  private statusVal: WsStatus = 'closed';
  private readonly messageListeners = new Set<(msg: WsInbound) => void>();
  private readonly statusListeners = new Set<(status: WsStatus) => void>();
  private readonly channels = new Set<string>();
  private reconnectAttempt = 0;
  private reconnectHandle: number | null = null;
  private heartbeatHandle: number | null = null;
  private lastSeenAt = 0;
  private intentionalClose = false;

  constructor(options: WsClientOptions) {
    this.url = options.url;
    this.socketFactory = options.socketFactory ?? ((url) => new WebSocket(url));
    this.clock = options.clock ?? realClock;
    this.pingIntervalMs = options.pingIntervalMs ?? DEFAULTS.pingIntervalMs;
    this.staleTimeoutMs = options.staleTimeoutMs ?? DEFAULTS.staleTimeoutMs;
    this.baseBackoffMs = options.baseBackoffMs ?? DEFAULTS.baseBackoffMs;
    this.maxBackoffMs = options.maxBackoffMs ?? DEFAULTS.maxBackoffMs;
    this.debug = options.debug ?? false;
  }

  /** 当前连接状态。 */
  get status(): WsStatus {
    return this.statusVal;
  }

  /** 订阅下行消息，返回取消订阅函数（监听器之间互不干扰）。 */
  onMessage(listener: (msg: WsInbound) => void): () => void {
    this.messageListeners.add(listener);
    return () => {
      this.messageListeners.delete(listener);
    };
  }

  /** 订阅状态变化，返回取消订阅函数；订阅时立即回放当前状态。 */
  onStatus(listener: (status: WsStatus) => void): () => void {
    this.statusListeners.add(listener);
    listener(this.statusVal);
    return () => {
      this.statusListeners.delete(listener);
    };
  }

  /** 建立连接（幂等：已在连接中/已连接时不做任何事）。 */
  connect(): void {
    if (this.statusVal === 'open' || this.statusVal === 'connecting') return;
    this.intentionalClose = false;
    this.openSocket();
  }

  /** 主动断开：不触发重连，并清空重连退避计数。 */
  disconnect(): void {
    this.intentionalClose = true;
    this.clearReconnect();
    this.stopHeartbeat();
    const sock = this.socket;
    this.socket = null;
    if (sock) {
      sock.onopen = null;
      sock.onmessage = null;
      sock.onclose = null;
      sock.onerror = null;
      try {
        sock.close();
      } catch {
        /* 忽略关闭异常 */
      }
    }
    this.reconnectAttempt = 0;
    this.setStatus('closed');
  }

  /** 上行消息；仅在连接打开时发送，未连接返回 false。 */
  send(msg: WsOutbound): boolean {
    if (this.statusVal !== 'open' || !this.socket) return false;
    try {
      this.socket.send(JSON.stringify(msg));
      return true;
    } catch {
      return false;
    }
  }

  /** 订阅频道（记住集合，重连后自动重订阅）。 */
  subscribe(channels: string[]): void {
    const fresh = channels.filter((channel) => !this.channels.has(channel));
    for (const channel of fresh) this.channels.add(channel);
    if (fresh.length > 0 && this.statusVal === 'open') {
      this.send({ type: 'subscribe', channels: fresh });
    }
  }

  /** 退订频道。 */
  unsubscribe(channels: string[]): void {
    const known = channels.filter((channel) => this.channels.delete(channel));
    if (known.length > 0 && this.statusVal === 'open') {
      this.send({ type: 'unsubscribe', channels: known });
    }
  }

  /** 当前已订阅频道（副本）。 */
  get subscribedChannels(): string[] {
    return [...this.channels];
  }

  private log(...args: unknown[]): void {
    if (this.debug) console.info('[ws]', ...args);
  }

  private openSocket(): void {
    this.setStatus('connecting');
    let sock: WebSocket;
    try {
      // 把 access token 加到 querystring（后端 WS 通过 `?token=` 鉴权）；
      // 每个浏览器 sessionStorage 中的 token 在 connect 时即时拼装。
      const token = getAccessToken();
      const url = token ? withToken(this.url, token) : this.url;
      sock = this.socketFactory(url);
    } catch (error) {
      this.log('socket factory failed', error);
      this.setStatus('closed');
      this.scheduleReconnect();
      return;
    }
    this.socket = sock;

    sock.onopen = () => {
      this.reconnectAttempt = 0;
      this.lastSeenAt = this.clock.now();
      this.setStatus('open');
      this.resubscribe();
      this.startHeartbeat();
    };
    sock.onmessage = (event: MessageEvent) => this.handleMessage(event);
    sock.onerror = () => {
      /* onclose 紧随其后，由 onclose 统一处理 */
    };
    sock.onclose = () => {
      this.stopHeartbeat();
      if (this.socket === sock) this.socket = null;
      this.setStatus('closed');
      if (!this.intentionalClose) this.scheduleReconnect();
    };
  }

  private handleMessage(event: MessageEvent): void {
    this.lastSeenAt = this.clock.now();
    let payload: WsInbound;
    try {
      payload = JSON.parse(String(event.data)) as WsInbound;
    } catch (error) {
      this.log('parse error', error);
      return;
    }
    for (const listener of [...this.messageListeners]) {
      try {
        listener(payload);
      } catch (error) {
        console.error('[ws] listener error', error);
      }
    }
  }

  private resubscribe(): void {
    if (this.channels.size === 0) return;
    this.send({ type: 'subscribe', channels: [...this.channels] });
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeatHandle = this.clock.setInterval(() => {
      if (this.clock.now() - this.lastSeenAt > this.staleTimeoutMs) {
        this.log('stale connection, forcing reconnect');
        this.forceClose();
        return;
      }
      this.send({ type: 'ping' });
    }, this.pingIntervalMs);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatHandle !== null) {
      this.clock.clearInterval(this.heartbeatHandle);
      this.heartbeatHandle = null;
    }
  }

  /** 半开连接：不置 intentionalClose，close 后由 onclose 走重连。 */
  private forceClose(): void {
    const sock = this.socket;
    if (!sock) return;
    try {
      sock.close();
    } catch {
      /* onclose 兜底 */
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectHandle !== null) return;
    const delay = Math.min(this.baseBackoffMs * 2 ** this.reconnectAttempt, this.maxBackoffMs);
    this.reconnectAttempt += 1;
    this.log(`reconnect in ${delay}ms (attempt ${this.reconnectAttempt})`);
    this.reconnectHandle = this.clock.setTimeout(() => {
      this.reconnectHandle = null;
      if (!this.intentionalClose) this.openSocket();
    }, delay);
  }

  private clearReconnect(): void {
    if (this.reconnectHandle !== null) {
      this.clock.clearTimeout(this.reconnectHandle);
      this.reconnectHandle = null;
    }
  }

  private setStatus(status: WsStatus): void {
    if (this.statusVal === status) return;
    this.statusVal = status;
    for (const listener of [...this.statusListeners]) {
      try {
        listener(status);
      } catch (error) {
        console.error('[ws] status listener error', error);
      }
    }
  }
}

/** 把 access token 拼到 WS URL 的 querystring 中。 */
function withToken(url: string, token: string): string {
  try {
    const u = new URL(url, typeof window === 'undefined' ? undefined : window.location.origin);
    u.searchParams.set('token', token);
    return u.toString();
  } catch {
    const sep = url.includes('?') ? '&' : '?';
    return `${url}${sep}token=${encodeURIComponent(token)}`;
  }
}

let singleton: WsClient | null = null;

/**
 * 全局共享的 WS 客户端（惰性创建，不在模块加载期触碰 window）。
 *
 * 不主动 `connect()`：连接由使用方显式发起。
 */
export function getWsClient(): WsClient {
  if (!singleton) singleton = new WsClient({ url: getWsUrl() });
  return singleton;
}

