/**
 * Agent REST 端点封装：会话、消息、工具、技能与危险操作确认。
 *
 * - 复用 `lib/api.ts` 的 `request`（统一 base、鉴权头、401 处理），**不修改它**。
 * - 对话流（SSE）**不在这里**，见 `lib/agentStream.ts`。
 */

import { request } from './api';
import type {
  AgentMessage,
  AgentMessageRecord,
  AgentSession,
  AgentSkill,
  AgentToolInfo,
} from '@/types/agent';

/** 会话列表响应。 */
export interface AgentSessionsResponse {
  items: AgentSession[];
}

/** 消息历史响应。 */
export interface AgentMessagesResponse {
  session_id: string;
  items: AgentMessageRecord[];
}

/** 工具清单响应。 */
export interface AgentToolsResponse {
  items: AgentToolInfo[];
}

/** 技能清单响应。 */
export interface AgentSkillsResponse {
  items: AgentSkill[];
}

/** 确认执行结果（镜像 `AgentConfirmResponse`）。 */
export interface AgentConfirmResult {
  ok: boolean;
  tool: string;
  result: unknown;
}

const seg = (value: string) => encodeURIComponent(value);

/** Agent 端点集合（后端路由自带 `/api` 前缀，由 `request` 的 base 补全）。 */
export const agentApi = {
  createSession: (title?: string | null) =>
    request<AgentSession>('/agent/sessions', { json: { title: title ?? null } }),
  listSessions: (signal?: AbortSignal) =>
    request<AgentSessionsResponse>('/agent/sessions', { signal }),
  getMessages: (sessionId: string, signal?: AbortSignal) =>
    request<AgentMessagesResponse>(`/agent/sessions/${seg(sessionId)}/messages`, { signal }),
  listTools: (signal?: AbortSignal) => request<AgentToolsResponse>('/agent/tools', { signal }),
  listSkills: (signal?: AbortSignal) => request<AgentSkillsResponse>('/agent/skills', { signal }),
  confirm: (sessionId: string, token: string) =>
    request<AgentConfirmResult>(`/agent/sessions/${seg(sessionId)}/confirm`, {
      json: { token },
    }),
};

/** Agent API 依赖类型（hook 测试可注入替身）。 */
export type AgentApi = typeof agentApi;

/** 把服务端消息记录映射为前端渲染模型。 */
export function toAgentMessage(record: AgentMessageRecord): AgentMessage {
  const role: AgentMessage['role'] =
    record.role === 'user' || record.role === 'tool' ? record.role : 'assistant';
  const toolCalls = (record.tool_calls ?? []).map((raw, index) => ({
    id: `history-${record.id}-${index}`,
    tool: typeof raw.name === 'string' ? raw.name : 'tool',
    arguments:
      typeof raw.arguments === 'object' && raw.arguments !== null
        ? (raw.arguments as Record<string, unknown>)
        : {},
    status: 'ok' as const,
    startedAt: 0,
  }));
  return {
    id: `srv-${record.id}`,
    role,
    content: record.content ?? '',
    created_at: record.created_at,
    tokens: record.tokens,
    toolCalls,
  };
}
