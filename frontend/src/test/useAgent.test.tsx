/**
 * useAgent：脚本化事件序列 → 消息/工具调用状态、内联错误、HITL 确认、预算提示与中断。
 *
 * 通过注入 `api` 与 `stream` 替身驱动，无真实网络。
 */

import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useAgent, type AgentController } from '@/hooks/useAgent';
import type { AgentApi } from '@/lib/agentApi';
import type { AgentStreamParams } from '@/lib/agentStream';
import type { AgentEvent } from '@/types/agent';

function createApi(): AgentApi {
  return {
    createSession: vi.fn().mockResolvedValue({ session_id: 's1', title: null, created_at: null }),
    listSessions: vi.fn().mockResolvedValue({ items: [] }),
    getMessages: vi.fn().mockResolvedValue({ session_id: 's1', items: [] }),
    listTools: vi.fn().mockResolvedValue({ items: [] }),
    listSkills: vi.fn().mockResolvedValue({ items: [] }),
    confirm: vi.fn().mockResolvedValue({ ok: true, tool: 'delete_strategy', result: null }),
  };
}

type Stream = (params: AgentStreamParams) => AsyncGenerator<AgentEvent>;

/** 按调用次序回放脚本的假流（超出后重复最后一段）。 */
function streamer(scripts: AgentEvent[][]): Stream {
  let index = 0;
  return () => {
    const script = scripts[Math.min(index, scripts.length - 1)] ?? [];
    index += 1;
    return (async function* () {
      for (const event of script) yield event;
    })();
  };
}

async function send(
  result: { current: AgentController },
  text: string,
): Promise<void> {
  await act(async () => {
    result.current.sendMessage(text);
  });
  await waitFor(() => expect(result.current.isStreaming).toBe(false));
}

describe('useAgent', () => {
  it('脚本化事件序列累积为消息与工具调用状态', async () => {
    const api = createApi();
    const stream = streamer([
      [
        { type: 'token', data: { text: '你好' } },
        { type: 'token', data: { text: '，世界' } },
        { type: 'tool_call_start', data: { tool: 'query_stocks', arguments: { q: '600' } } },
        { type: 'tool_call_result', data: { tool: 'query_stocks', ok: true, summary: '3 条' } },
        { type: 'done', data: { reason: 'completed', message: '你好，世界' } },
      ],
    ]);
    const { result } = renderHook(() => useAgent({ api, stream }));
    await send(result, 'hi');
    await waitFor(() => expect(result.current.messages).toHaveLength(2));

    const [user, assistant] = result.current.messages;
    expect(user).toMatchObject({ role: 'user', content: 'hi' });
    expect(assistant).toMatchObject({ role: 'assistant', content: '你好，世界', streaming: false });
    expect(assistant.toolCalls).toHaveLength(1);
    expect(assistant.toolCalls?.[0]).toMatchObject({
      tool: 'query_stocks',
      status: 'ok',
      summary: '3 条',
    });
    expect(api.createSession).toHaveBeenCalledTimes(1);
  });

  it('被拒的工具调用标记为 denied', async () => {
    const api = createApi();
    const stream = streamer([
      [
        { type: 'tool_call_start', data: { tool: 'delete_strategy', arguments: { id: 'S4' } } },
        { type: 'tool_call_result', data: { tool: 'delete_strategy', ok: false, denied: true, summary: '无权调用工具' } },
        { type: 'done', data: { reason: 'completed' } },
      ],
    ]);
    const { result } = renderHook(() => useAgent({ api, stream }));
    await send(result, '删除策略');
    expect(result.current.messages[1].toolCalls?.[0].status).toBe('denied');
  });

  it('error 事件设置内联错误，且会话仍可继续使用', async () => {
    const api = createApi();
    const stream = streamer([
      [
        { type: 'error', data: { code: 'model_error', message: '模型调用失败：超时' } },
        { type: 'done', data: { reason: 'model_error' } },
      ],
      [
        { type: 'token', data: { text: '恢复' } },
        { type: 'done', data: { reason: 'completed' } },
      ],
    ]);
    const { result } = renderHook(() => useAgent({ api, stream }));

    await send(result, '第一次');
    expect(result.current.error?.message).toBe('模型调用失败：超时');
    expect(result.current.isStreaming).toBe(false);

    act(() => result.current.dismissError());
    expect(result.current.error).toBeNull();

    await send(result, '第二次');
    await waitFor(() => expect(result.current.messages).toHaveLength(4));
    expect(result.current.error).toBeNull();
    expect(result.current.messages[3].content).toBe('恢复');
    // 会话复用，不重复创建
    expect(api.createSession).toHaveBeenCalledTimes(1);
  });

  it('confirmation_required 登记待办，confirm() 才调用确认端点', async () => {
    const api = createApi();
    const stream = streamer([
      [
        {
          type: 'confirmation_required',
          data: { token: 'tok-1', tool: 'delete_strategy', arguments: { id: 'S4' } },
        },
        { type: 'done', data: { reason: 'completed' } },
      ],
    ]);
    const { result } = renderHook(() => useAgent({ api, stream }));
    await send(result, '删除策略 S4');

    expect(result.current.pendingAction).toMatchObject({ token: 'tok-1', tool: 'delete_strategy' });
    expect(api.confirm).not.toHaveBeenCalled();

    await act(async () => {
      result.current.confirm();
    });
    await waitFor(() => expect(api.confirm).toHaveBeenCalledWith('s1', 'tok-1'));
    await waitFor(() => expect(result.current.pendingAction).toBeNull());
  });

  it('预算耗尽的 done 作为信息提示而非错误', async () => {
    const api = createApi();
    const stream = streamer([
      [
        {
          type: 'done',
          data: { reason: 'budget_exhausted', message: '已达最大轮数（12），已终止本次会话' },
        },
      ],
    ]);
    const { result } = renderHook(() => useAgent({ api, stream }));
    await send(result, 'hi');

    expect(result.current.error).toBeNull();
    expect(result.current.notice).toContain('已达最大轮数');
  });

  it('stop() 中断流式读取并复位状态', async () => {
    const api = createApi();
    const stream: Stream = (params) =>
      (async function* () {
        yield { type: 'token', data: { text: 'A' } };
        await new Promise<void>((resolve) => {
          if (params.signal?.aborted) resolve();
          else params.signal?.addEventListener('abort', () => resolve());
        });
      })();

    const { result } = renderHook(() => useAgent({ api, stream }));
    await act(async () => {
      result.current.sendMessage('hi');
    });
    await waitFor(() => expect(result.current.isStreaming).toBe(true));

    act(() => result.current.stop());
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    expect(result.current.messages[1].content).toBe('A');
  });

  it('按角色过滤的工具清单直接反映在状态中（非 admin 无变更工具）', async () => {
    const api = createApi();
    api.listTools = vi.fn().mockResolvedValue({
      items: [
        {
          name: 'query_stocks',
          description: '检索股票',
          mutating: false,
          requires_confirmation: false,
          required_role: null,
        },
      ],
    });
    const { result } = renderHook(() => useAgent({ api, stream: streamer([[]]) }));
    await waitFor(() => expect(result.current.tools).toHaveLength(1));
    expect(result.current.tools.every((tool) => !tool.mutating)).toBe(true);
  });
});
