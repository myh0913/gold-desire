import { beforeEach, describe, expect, it, vi } from 'vitest';
import { WsClient, type WsClock } from '../ws';
import type { WsInbound, WsStatus } from '@/types';

/** 确定性时钟：可手动推进，避免真实计时器带来的抖动。 */
class FakeClock implements WsClock {
  time = 0;
  private seq = 0;
  private readonly timeouts = new Map<number, { at: number; fn: () => void }>();
  private readonly intervals = new Map<number, { every: number; next: number; fn: () => void }>();

  now(): number {
    return this.time;
  }

  setTimeout(fn: () => void, ms: number): number {
    const id = ++this.seq;
    this.timeouts.set(id, { at: this.time + ms, fn });
    return id;
  }

  clearTimeout(handle: number): void {
    this.timeouts.delete(handle);
  }

  setInterval(fn: () => void, ms: number): number {
    const id = ++this.seq;
    this.intervals.set(id, { every: ms, next: this.time + ms, fn });
    return id;
  }

  clearInterval(handle: number): void {
    this.intervals.delete(handle);
  }

  /** 推进时间并按到期顺序依次触发回调。 */
  advance(ms: number): void {
    const target = this.time + ms;
    for (;;) {
      let nextAt = Number.POSITIVE_INFINITY;
      let nextId = -1;
      let nextKind: 'timeout' | 'interval' | null = null;

      for (const [id, entry] of this.timeouts) {
        if (entry.at <= target && entry.at < nextAt) {
          nextAt = entry.at;
          nextId = id;
          nextKind = 'timeout';
        }
      }
      for (const [id, entry] of this.intervals) {
        if (entry.next <= target && entry.next < nextAt) {
          nextAt = entry.next;
          nextId = id;
          nextKind = 'interval';
        }
      }
      if (nextKind === null) break;

      this.time = nextAt;
      if (nextKind === 'timeout') {
        const entry = this.timeouts.get(nextId);
        this.timeouts.delete(nextId);
        entry?.fn();
      } else {
        const entry = this.intervals.get(nextId);
        if (entry) {
          entry.next = this.time + entry.every;
          entry.fn();
        }
      }
    }
    this.time = target;
  }
}

/** 可手动驱动的 WebSocket 替身。 */
class FakeSocket {
  static instances: FakeSocket[] = [];

  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readonly sent: string[] = [];
  closed = false;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.closed = true;
    this.onclose?.();
  }

  // ---- 测试驱动 ----
  open(): void {
    this.onopen?.();
  }

  deliver(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
  }

  serverClose(): void {
    this.closed = true;
    this.onclose?.();
  }
}

const BASE = 1_000;
const MAX = 4_000;

function createClient(clock: FakeClock, overrides: Partial<{ pingIntervalMs: number; staleTimeoutMs: number }> = {}) {
  return new WsClient({
    url: 'ws://localhost:8000/ws',
    clock,
    socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket,
    baseBackoffMs: BASE,
    maxBackoffMs: MAX,
    pingIntervalMs: overrides.pingIntervalMs ?? 30_000,
    staleTimeoutMs: overrides.staleTimeoutMs ?? 60_000,
  });
}

describe('WsClient', () => {
  beforeEach(() => {
    FakeSocket.instances = [];
  });

  it('连接成功后状态为 open，并重置退避计数', () => {
    const clock = new FakeClock();
    const client = createClient(clock);

    client.connect();
    expect(client.status).toBe('connecting');
    expect(FakeSocket.instances).toHaveLength(1);

    FakeSocket.instances[0].open();
    expect(client.status).toBe('open');
  });

  it('断线后按指数退避重连并在上限封顶', () => {
    const clock = new FakeClock();
    const client = createClient(clock, { pingIntervalMs: 1_000_000, staleTimeoutMs: 1_000_000 });

    client.connect();
    FakeSocket.instances[0].open();
    FakeSocket.instances[0].serverClose();
    expect(client.status).toBe('closed');

    clock.advance(BASE - 1);
    expect(FakeSocket.instances).toHaveLength(1);
    clock.advance(1);
    expect(FakeSocket.instances).toHaveLength(2); // 1000ms

    FakeSocket.instances[1].serverClose();
    clock.advance(2 * BASE);
    expect(FakeSocket.instances).toHaveLength(3); // 2000ms

    FakeSocket.instances[2].serverClose();
    clock.advance(MAX);
    expect(FakeSocket.instances).toHaveLength(4); // 4000ms

    FakeSocket.instances[3].serverClose();
    clock.advance(MAX);
    expect(FakeSocket.instances).toHaveLength(5); // 封顶 4000ms（而非 8000ms）

    // 重连成功后计数归零 → 下一次退避回到基数
    FakeSocket.instances[4].open();
    FakeSocket.instances[4].serverClose();
    clock.advance(BASE);
    expect(FakeSocket.instances).toHaveLength(6);
  });

  it('主动断开不触发重连', () => {
    const clock = new FakeClock();
    const client = createClient(clock);

    client.connect();
    FakeSocket.instances[0].open();
    client.disconnect();

    clock.advance(1_000_000);
    expect(FakeSocket.instances).toHaveLength(1);
    expect(client.status).toBe('closed');
  });

  it('按间隔发送心跳 ping', () => {
    const clock = new FakeClock();
    const client = createClient(clock, { pingIntervalMs: 30_000, staleTimeoutMs: 600_000 });

    client.connect();
    const socket = FakeSocket.instances[0];
    socket.open();

    clock.advance(30_000);
    expect(socket.sent).toEqual([JSON.stringify({ type: 'ping' })]);

    clock.advance(30_000);
    expect(socket.sent).toHaveLength(2);
  });

  it('长时间无下行消息判定半开并强制重连', () => {
    const clock = new FakeClock();
    const client = createClient(clock, { pingIntervalMs: 30_000, staleTimeoutMs: 45_000 });

    client.connect();
    const socket = FakeSocket.instances[0];
    socket.open();

    clock.advance(30_000);
    expect(socket.closed).toBe(false);

    clock.advance(30_000); // 距上次收到消息已 60s > 45s
    expect(socket.closed).toBe(true);
    expect(client.status).toBe('closed');

    clock.advance(BASE);
    expect(FakeSocket.instances).toHaveLength(2);
  });

  it('收到任意下行消息会刷新存活时间', () => {
    const clock = new FakeClock();
    const client = createClient(clock, { pingIntervalMs: 30_000, staleTimeoutMs: 45_000 });

    client.connect();
    const socket = FakeSocket.instances[0];
    socket.open();

    clock.advance(30_000);
    socket.deliver({ type: 'heartbeat', ts: 1 });
    clock.advance(30_000); // 距最后消息仅 30s
    expect(socket.closed).toBe(false);
  });

  it('监听器相互隔离：一个抛错不影响其他', () => {
    const clock = new FakeClock();
    const client = createClient(clock);
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    const bad = vi.fn(() => {
      throw new Error('boom');
    });
    const received: WsInbound[] = [];
    const good = vi.fn((msg: WsInbound) => received.push(msg));

    client.onMessage(bad);
    client.onMessage(good);

    client.connect();
    FakeSocket.instances[0].open();
    FakeSocket.instances[0].deliver({ type: 'heartbeat', ts: 7 });

    expect(bad).toHaveBeenCalledTimes(1);
    expect(good).toHaveBeenCalledTimes(1);
    expect(received).toEqual([{ type: 'heartbeat', ts: 7 }]);

    errorSpy.mockRestore();
  });

  it('订阅状态自持：重连后自动重订阅，且只发送新增频道', () => {
    const clock = new FakeClock();
    const client = createClient(clock, { pingIntervalMs: 1_000_000, staleTimeoutMs: 1_000_000 });

    client.subscribe(['advice', 'alert']);
    expect(FakeSocket.instances).toHaveLength(0);

    client.connect();
    const first = FakeSocket.instances[0];
    first.open();
    expect(first.sent).toEqual([
      JSON.stringify({ type: 'subscribe', channels: ['advice', 'alert'] }),
    ]);

    client.subscribe(['alert', 'ingest']); // alert 已订阅，只发 ingest
    expect(first.sent[1]).toBe(JSON.stringify({ type: 'subscribe', channels: ['ingest'] }));
    expect(client.subscribedChannels).toEqual(['advice', 'alert', 'ingest']);

    first.serverClose();
    clock.advance(BASE);
    const second = FakeSocket.instances[1];
    second.open();
    expect(second.sent).toEqual([
      JSON.stringify({ type: 'subscribe', channels: ['advice', 'alert', 'ingest'] }),
    ]);
  });

  it('status 可观察：订阅时回放当前值，随后推送变化', () => {
    const clock = new FakeClock();
    const client = createClient(clock);
    const seen: WsStatus[] = [];

    const unsubscribe = client.onStatus((status) => seen.push(status));
    expect(seen).toEqual(['closed']);

    client.connect();
    FakeSocket.instances[0].open();
    expect(seen).toEqual(['closed', 'connecting', 'open']);

    unsubscribe();
    client.disconnect();
    expect(seen).toEqual(['closed', 'connecting', 'open']);
  });

  it('未连接时 send 返回 false', () => {
    const clock = new FakeClock();
    const client = createClient(clock);
    expect(client.send({ type: 'ping' })).toBe(false);
  });
});
