/**
 * agentStream：增量 SSE 解析、畸形帧容错、中断与提前关闭。
 *
 * 全部通过注入的 `fetchImpl` 驱动（返回 ReadableStream），无真实网络。
 */

import { describe, expect, it, vi } from 'vitest';
import { streamAgentChat } from '@/lib/agentStream';
import type { AgentEvent } from '@/types/agent';

const encoder = new TextEncoder();

/** 立即返回若干 chunk 后关闭的响应。 */
function chunkedResponse(chunks: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return { ok: true, status: 200, body: stream } as unknown as Response;
}

/** 不关闭的响应；`signal` 中止时以 AbortError 终止流（模拟真实 fetch）。 */
function pendingResponse(signal: AbortSignal, chunks: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      signal.addEventListener('abort', () =>
        controller.error(new DOMException('Aborted', 'AbortError')),
      );
    },
  });
  return { ok: true, status: 200, body: stream } as unknown as Response;
}

const params = (chunks: string[], extra: Record<string, unknown> = {}) => ({
  sessionId: 's1',
  message: 'hi',
  getToken: () => null,
  fetchImpl: (async () => chunkedResponse(chunks)) as unknown as typeof fetch,
  ...extra,
});

async function collect(gen: AsyncGenerator<AgentEvent>): Promise<AgentEvent[]> {
  const events: AgentEvent[] = [];
  for await (const event of gen) events.push(event);
  return events;
}

describe('streamAgentChat', () => {
  it('解析被切在帧中间的 chunk，产出完整事件序列', async () => {
    const events = await collect(
      streamAgentChat(
        params([
          'data: {"type":"token","data":{"text":"你',
          '好"}}\n\ndata: {"type":"tool_call_st',
          'art","data":{"tool":"query_stocks","arguments":{"q":"600"}}}\n\n',
          'data: {"type":"done","data":{"reason":"completed"}}\n\n',
        ]),
      ),
    );

    expect(events).toEqual([
      { type: 'token', data: { text: '你好' } },
      { type: 'tool_call_start', data: { tool: 'query_stocks', arguments: { q: '600' } } },
      { type: 'done', data: { reason: 'completed' } },
    ]);
  });

  it('单 chunk 内含多个事件与 [DONE] 哨兵', async () => {
    const events = await collect(
      streamAgentChat(
        params([
          'data: {"type":"token","data":{"text":"A"}}\n\n' +
            'data: {"type":"token","data":{"text":"B"}}\n\n' +
            'data: [DONE]\n\n',
        ]),
      ),
    );

    expect(events.map((event) => event.type)).toEqual(['token', 'token', 'done']);
    expect(events[2]).toEqual({ type: 'done', data: { reason: 'completed' } });
  });

  it('畸形帧与未知事件类型被跳过，不影响后续事件', async () => {
    const onWarn = vi.fn();
    const events = await collect(
      streamAgentChat(
        params(
          [
            'data: {不是 JSON\n\n',
            'data: {"type":"mystery","data":{}}\n\n',
            ':keep-alive\n\n',
            'data: {"type":"done","data":{"reason":"completed"}}\n\n',
          ],
          { onWarn },
        ),
      ),
    );

    expect(events).toEqual([{ type: 'done', data: { reason: 'completed' } }]);
    expect(onWarn).toHaveBeenCalledTimes(2);
  });

  it('abort 立即停止读取，且不产出错误事件', async () => {
    const controller = new AbortController();
    const iterator = streamAgentChat({
      sessionId: 's1',
      message: 'hi',
      signal: controller.signal,
      getToken: () => null,
      fetchImpl: (async () =>
        pendingResponse(controller.signal, [
          'data: {"type":"token","data":{"text":"A"}}\n\n',
        ])) as unknown as typeof fetch,
    })[Symbol.asyncIterator]();

    const first = await iterator.next();
    expect(first.value).toEqual({ type: 'token', data: { text: 'A' } });

    controller.abort();
    const second = await iterator.next();
    expect(second.done).toBe(true);
  });

  it('提前关闭（未收到 done）产出明确的 error 事件', async () => {
    const events = await collect(
      streamAgentChat(params(['data: {"type":"token","data":{"text":"A"}}\n\n'])),
    );

    expect(events).toEqual([
      { type: 'token', data: { text: 'A' } },
      { type: 'error', data: { code: 'stream_closed', message: '连接提前中断，未收到完成事件' } },
      { type: 'done', data: { reason: 'error' } },
    ]);
  });

  it('非 2xx 响应产出 http_error 事件', async () => {
    const events = await collect(
      streamAgentChat({
        sessionId: 's1',
        message: 'hi',
        getToken: () => null,
        fetchImpl: (async () =>
          ({
            ok: false,
            status: 403,
            text: async () => JSON.stringify({ error: { message: '无权访问该会话' } }),
          }) as unknown as Response) as unknown as typeof fetch,
      }),
    );

    expect(events[0]).toEqual({
      type: 'error',
      data: { code: 'http_error', message: 'Agent 服务返回 403：无权访问该会话' },
    });
    expect(events[1]).toEqual({ type: 'done', data: { reason: 'error' } });
  });
});
