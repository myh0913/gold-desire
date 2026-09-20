/**
 * 内置 Agent 契约：会话、消息、工具、技能与 SSE 事件。
 *
 * 逐字段镜像后端 `app/schemas/agent.py` 与 `app/agent/loop.py` 的事件载荷，
 * 字段名保持 snake_case，避免序列化时再做映射。
 */

/** 消息角色。 */
export type AgentRole = 'user' | 'assistant' | 'tool';

/** Agent 会话（镜像 `AgentSessionOut`）。 */
export interface AgentSession {
  session_id: string;
  title: string | null;
  created_at: string | null;
}

/**
 * 工具元数据（镜像 `AgentToolOut`，服务端已按调用者角色过滤）。
 *
 * 注意：后端 `GET /agent/tools` 目前**不下发** `parameters`（`public_dict()` 只含
 * name/description/mutating/requires_confirmation/required_role），故此处为可选。
 */
export interface AgentToolInfo {
  name: string;
  description: string;
  mutating: boolean;
  requires_confirmation: boolean;
  required_role: string | null;
  parameters?: Record<string, unknown> | null;
}

/** 预置技能（镜像 `AgentSkillOut`）。 */
export interface AgentSkill {
  name: string;
  description: string;
  tools: string[];
  output_format: string;
}

/** 工具调用状态：running=执行中 / ok=成功 / denied=被拒 / error=失败 / pending=待人工确认。 */
export type AgentToolCallStatus = 'running' | 'ok' | 'denied' | 'error' | 'pending';

/** 一次工具调用（前端渲染模型，由流事件累积而成）。 */
export interface AgentToolCall {
  id: string;
  tool: string;
  arguments: Record<string, unknown>;
  status: AgentToolCallStatus;
  /** 结果摘要（`tool_call_result` 事件携带） */
  summary?: string;
  /** 耗时（毫秒，前端按开始时间估算；结果事件不携带该字段） */
  durationMs?: number;
  /** 开始时刻（`Date.now()`，用于估算耗时） */
  startedAt: number;
}

/** 会话消息（前端渲染模型）。 */
export interface AgentMessage {
  id: string;
  role: AgentRole;
  content: string;
  created_at: string | null;
  tokens?: number;
  /** 本条 assistant 消息触发的工具调用 */
  toolCalls?: AgentToolCall[];
  /** 是否仍在流式接收（渲染光标） */
  streaming?: boolean;
}

/** 服务端消息记录（镜像 `AgentMessageOut`）。 */
export interface AgentMessageRecord {
  id: number;
  role: string;
  content: string | null;
  tool_calls: Array<Record<string, unknown>> | null;
  tokens: number;
  created_at: string | null;
}

/** 待人工确认的危险操作（`confirmation_required` 事件载荷）。 */
export interface AgentPendingAction {
  token: string;
  tool: string;
  arguments: Record<string, unknown>;
  expires_at?: string | null;
}

/** 会话结束原因（`done` 事件）。budget_exhausted 属**正常终止**，非错误。 */
export type AgentDoneReason = 'completed' | 'model_error' | 'budget_exhausted' | 'error';

/** 增量文本。 */
export interface AgentTokenEvent {
  type: 'token';
  data: { text: string };
}

/** 工具调用开始。 */
export interface AgentToolStartEvent {
  type: 'tool_call_start';
  data: { tool: string; arguments?: Record<string, unknown> };
}

/** 工具调用结束。 */
export interface AgentToolResultEvent {
  type: 'tool_call_result';
  data: {
    tool: string;
    ok: boolean;
    summary?: string;
    denied?: boolean;
    pending_confirmation?: boolean;
    code?: string;
  };
}

/** 危险操作待人工确认。 */
export interface AgentConfirmationEvent {
  type: 'confirmation_required';
  data: {
    token: string;
    tool: string;
    arguments?: Record<string, unknown>;
    expires_at?: string | null;
  };
}

/** 错误（模型失败 / 越权 / 内部错误 / 流中断）。 */
export interface AgentErrorEvent {
  type: 'error';
  data: { code: string; message: string; tool?: string };
}

/** 流结束。 */
export interface AgentDoneEvent {
  type: 'done';
  data: { reason: AgentDoneReason; message?: string };
}

/** SSE 流事件（判别联合，与后端 `AgentEvent` 六类一一对应）。 */
export type AgentEvent =
  | AgentTokenEvent
  | AgentToolStartEvent
  | AgentToolResultEvent
  | AgentConfirmationEvent
  | AgentErrorEvent
  | AgentDoneEvent;

/** 事件类型名。 */
export type AgentEventType = AgentEvent['type'];

/** 抽屉内联错误（可关闭、可重试）。 */
export interface AgentInlineError {
  code: string;
  message: string;
  /** 是否展示重试入口 */
  retryable?: boolean;
}
