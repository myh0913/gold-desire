/**
 * Agent 对话 SSE 客户端（`POST` + `ReadableStream`）。
 *
 * 为什么不用 `EventSource`：需要 POST 请求体与 `Authorization` 头。
 *
 * 设计要点：
 * - **增量解析**：帧可能被切在 chunk 边界中间，:class:`SseFrameParser` 按行缓冲，
 *   只在遇到空行（SSE 帧分隔）时产出完整帧；`event:` / `data:` / `:` 注释均可处理。
 * - **畸形帧不致命**：JSON 解析失败或未知事件类型 → 记日志并跳过，绝不抛出。
 * - **提前关闭**：流结束但未收到 `done` → 产出明确的 `error` 事件（`stream_closed`）。
 * - **可中断**：调用方传 `AbortSignal`（停止按钮），中止即静默结束，不产出错误。
 * - **可注入**：`fetchImpl` / `baseUrl` / `getToken` 均可替换，模块无全局状态。
 */

import { getApiBaseUrl } from './api';
import { getAccessToken } from './tokenStore';
import type { AgentEvent, AgentToolCall, AgentToolResultEvent } from '@/types/agent';

/** 已解析的原始 SSE 帧。 */
export interface SseFrame {
  event: string | null;
  data: string;
}

/** 后端六类事件名（白名单）。 */
const EVENT_TYPES = new Set<string>([
  'token',
  'tool_call_start',
  'tool_call_result',
  'confirmation_required',
  'error',
  'done',
]);

/** 增量 SSE 帧解析器：按行缓冲，空行分隔帧。 */
export class SseFrameParser {
  private buffer = '';
  private dataLines: string[] = [];
  private eventName: string | null = null;

  /** 追加一段文本，返回本次可完整解析的帧（可能为空）。 */
  push(chunk: string): SseFrame[] {
    this.buffer += chunk;
    const frames: SseFrame[] = [];
    for (;;) {
      const newline = this.buffer.indexOf('\n');
      if (newline < 0) break;
      const raw = this.buffer.slice(0, newline);
      this.buffer = this.buffer.slice(newline + 1);
      const line = raw.endsWith('\r') ? raw.slice(0, -1) : raw;
      if (line === '') {
        const frame = this.flush();
        if (frame) frames.push(frame);
        continue;
      }
      if (line.startsWith(':')) continue; // 注释行
      const colon = line.indexOf(':');
      const field = colon < 0 ? line : line.slice(0, colon);
      let value = colon < 0 ? '' : line.slice(colon + 1);
      if (value.startsWith(' ')) value = value.slice(1);
      if (field === 'event') this.eventName = value;
      else if (field === 'data') this.dataLines.push(value);
      // 其余字段（id / retry）忽略
    }
    return frames;
  }

  private flush(): SseFrame | null {
    if (this.eventName === null && this.dataLines.length === 0) return null;
    const frame: SseFrame = { event: this.eventName, data: this.dataLines.join('\n') };
    this.eventName = null;
    this.dataLines = [];
    return frame;
  }
}

/** 把原始帧转成类型化事件；畸形/未知帧返回 `null`（调用方记日志）。 */
export function toAgentEvent(frame: SseFrame): AgentEvent | null {
  const data = frame.data.trim();
  if (data === '[DONE]') return { type: 'done', data: { reason: 'completed' } };
  if (data === '') return null;

  let payload: unknown;
  try {
    payload = JSON.parse(data);
  } catch {
    return null;
  }
  if (typeof payload !== 'object' || payload === null) return null;

  const record = payload as { type?: unknown; data?: unknown };
  const type = typeof record.type === 'string' ? record.type : frame.event;
  if (!type || !EVENT_TYPES.has(type)) return null;

  const body =
    typeof record.data === 'object' && record.data !== null
      ? (record.data as Record<string, unknown>)
      : {};
  return { type, data: body } as AgentEvent;
}

/** 流式对话参数。 */
export interface AgentStreamParams {
  sessionId: string;
  message: string;
  skill?: string | null;
  /** 停止按钮绑定的中止信号 */
  signal?: AbortSignal;
  /** 注入点（测试用） */
  fetchImpl?: typeof fetch;
  baseUrl?: string;
  getToken?: () => string | null;
  /** 畸形帧 / 异常日志（默认 `console.warn`） */
  onWarn?: (message: string) => void;
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isAbort(error: unknown): boolean {
  return error instanceof Error && (error.name === 'AbortError' || error.name === 'TimeoutError');
}

function errorEvent(code: string, message: string): AgentEvent {
  return { type: 'error', data: { code, message } };
}

async function errorSuffix(response: Response): Promise<string> {
  try {
    const text = await response.text();
    if (!text) return '';
    const parsed = JSON.parse(text) as { error?: { message?: string } };
    return `：${parsed.error?.message ?? text.slice(0, 200)}`;
  } catch {
    return '';
  }
}

/**
 * 发起一轮对话，按序产出类型化事件（async generator）。
 *
 * 正常结束：产出 `done`；异常：产出 `error`（必要时补 `done`）；被中止：静默结束。
 */
export async function* streamAgentChat(params: AgentStreamParams): AsyncGenerator<AgentEvent> {
  if (params.signal?.aborted) return;

  const doFetch = params.fetchImpl ?? fetch;
  const baseUrl = params.baseUrl ?? getApiBaseUrl();
  const warn = params.onWarn ?? ((message: string) => console.warn('[agentStream]', message));
  const token = (params.getToken ?? getAccessToken)();

  const headers = new Headers({ Accept: 'text/event-stream', 'Content-Type': 'application/json' });
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const body: Record<string, unknown> = { message: params.message };
  if (params.skill) body.skill = params.skill;

  const url = `${baseUrl}/agent/sessions/${encodeURIComponent(params.sessionId)}/chat`;
  let response: Response;
  try {
    response = await doFetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      credentials: 'include',
      signal: params.signal,
    });
  } catch (error) {
    if (isAbort(error) || params.signal?.aborted) return;
    yield errorEvent('stream_failed', `无法连接 Agent 服务：${describe(error)}`);
    return;
  }

  if (!response.ok) {
    yield errorEvent('http_error', `Agent 服务返回 ${response.status}${await errorSuffix(response)}`);
    yield { type: 'done', data: { reason: 'error' } };
    return;
  }
  if (!response.body) {
    yield errorEvent('stream_failed', 'Agent 服务未返回流式响应体');
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseFrameParser();
  let sawDone = false;

  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      for (const frame of parser.push(decoder.decode(value, { stream: true }))) {
        const event = toAgentEvent(frame);
        if (!event) {
          warn(`跳过无法解析的 SSE 帧：${frame.data.slice(0, 200)}`);
          continue;
        }
        if (event.type === 'done') sawDone = true;
        yield event;
      }
    }
  } catch (error) {
    if (isAbort(error) || params.signal?.aborted) return;
    yield errorEvent('stream_failed', `读取流失败：${describe(error)}`);
    return;
  }

  if (!sawDone) {
    yield errorEvent('stream_closed', '连接提前中断，未收到完成事件');
    yield { type: 'done', data: { reason: 'error' } };
  }
}

// ------------------------------------------------------------ 事件归约辅助

let agentSeq = 0;

/** 单调递增的本地 id（消息 / 工具调用渲染用，不参与服务端交互）。 */
export function nextAgentId(prefix: string): string {
  agentSeq += 1;
  return `${prefix}-${agentSeq}`;
}

/** 找到最近一次仍处于 running 的同名工具调用。 */
function findRunning(calls: AgentToolCall[], tool: string): number {
  for (let i = calls.length - 1; i >= 0; i -= 1) {
    if (calls[i].tool === tool && calls[i].status === 'running') return i;
  }
  return -1;
}

/** 按 `tool_call_result` 结算一次工具调用（返回新数组，不改原数组）。 */
export function reduceToolCalls(
  calls: AgentToolCall[],
  event: AgentToolResultEvent,
): AgentToolCall[] {
  const index = findRunning(calls, event.data.tool);
  if (index < 0) return calls;
  const next = calls.slice();
  const target = next[index];
  const status = event.data.denied
    ? 'denied'
    : event.data.pending_confirmation
      ? 'pending'
      : event.data.ok
        ? 'ok'
        : 'error';
  next[index] = {
    ...target,
    status,
    summary: event.data.summary ?? target.summary,
    durationMs: Date.now() - target.startedAt,
  };
  return next;
}
