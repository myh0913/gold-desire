/**
 * 测试辅助：最小 WebSocket 替身。
 *
 * `lib/ws.ts` 的默认 socket 工厂在调用时解析全局 `WebSocket`，故在首次
 * `getWsClient()` 之前 `vi.stubGlobal('WebSocket', FakeWebSocket)` 即可生效。
 * 替身不会主动触发 `onclose`，因此不会产生重连定时器噪音。
 */

export class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  readonly url: string;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(): void {
    /* 记录发送内容对断言无必要，保持空实现 */
  }

  close(): void {
    this.readyState = 3;
    this.onclose?.();
  }
}
