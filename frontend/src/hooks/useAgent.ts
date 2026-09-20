/**
 * Agent 抽屉状态编排：会话生命周期、流式对话、工具调用、人工确认与内联错误。
 *
 * - 事件来源：`lib/agentStream`（可注入替身），本 hook 只做**状态归约**。
 * - 中断：`stop()` 触发 `AbortController`；全程无全局 loading，不阻塞主应用。
 * - 危险操作：`confirmation_required` 仅登记待办，`confirm()` 才真正执行。
 * - 预算耗尽（`done.reason === 'budget_exhausted'`）按**信息提示**处理，不算错误。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { agentApi, toAgentMessage, type AgentApi } from '@/lib/agentApi';
import {
  nextAgentId,
  reduceToolCalls,
  streamAgentChat,
  type AgentStreamParams,
} from '@/lib/agentStream';
import { errorMessage } from '@/components/common/StateViews';
import type {
  AgentEvent,
  AgentInlineError,
  AgentMessage,
  AgentPendingAction,
  AgentSession,
  AgentSkill,
  AgentToolInfo,
} from '@/types/agent';

/** hook 依赖注入（缺省走真实实现）。 */
export interface UseAgentOptions {
  /** 是否激活：抽屉打开时才拉取清单与会话，避免后台空转 */
  active?: boolean;
  api?: AgentApi;
  stream?: (params: AgentStreamParams) => AsyncGenerator<AgentEvent>;
  /** 新建会话的标题 */
  title?: string;
}

/** 抽屉控制器。 */
export interface AgentController {
  sessionId: string | null;
  sessions: AgentSession[];
  messages: AgentMessage[];
  tools: AgentToolInfo[];
  skills: AgentSkill[];
  isStreaming: boolean;
  isConfirming: boolean;
  isLoadingHistory: boolean;
  error: AgentInlineError | null;
  notice: string | null;
  pendingAction: AgentPendingAction | null;
  sendMessage: (text: string, skill?: string | null) => void;
  stop: () => void;
  confirm: () => void;
  cancelConfirmation: () => void;
  dismissError: () => void;
  dismissNotice: () => void;
  retry: () => void;
  selectSession: (sessionId: string) => void;
  newSession: () => void;
}

export function useAgent(options: UseAgentOptions = {}): AgentController {
  const api = options.api ?? agentApi;
  const stream = options.stream ?? streamAgentChat;
  const active = options.active ?? true;
  const title = options.title;

  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [tools, setTools] = useState<AgentToolInfo[]>([]);
  const [skills, setSkills] = useState<AgentSkill[]>([]);
  const [isStreaming, setStreaming] = useState(false);
  const [isConfirming, setConfirming] = useState(false);
  const [isLoadingHistory, setLoadingHistory] = useState(false);
  const [error, setError] = useState<AgentInlineError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<AgentPendingAction | null>(null);

  const sessionRef = useRef<string | null>(null);
  const pendingRef = useRef<AgentPendingAction | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const streamingRef = useRef(false);
  const lastRef = useRef<{ text: string; skill: string | null } | null>(null);

  const setCurrentSession = useCallback((id: string | null) => {
    sessionRef.current = id;
    setSessionId(id);
  }, []);

  const setPending = useCallback((action: AgentPendingAction | null) => {
    pendingRef.current = action;
    setPendingAction(action);
  }, []);

  // 清单：工具按角色过滤（服务端权威）、技能、会话列表
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    void (async () => {
      try {
        const [toolResp, skillResp, sessionResp] = await Promise.all([
          api.listTools(),
          api.listSkills(),
          api.listSessions(),
        ]);
        if (cancelled) return;
        setTools(toolResp.items);
        setSkills(skillResp.items);
        setSessions(sessionResp.items);
      } catch (err) {
        if (!cancelled) {
          setError({ code: 'metadata_failed', message: errorMessage(err), retryable: true });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [active, api]);

  const loadHistory = useCallback(
    async (id: string) => {
      setLoadingHistory(true);
      try {
        const resp = await api.getMessages(id);
        setMessages(resp.items.map(toAgentMessage));
      } catch (err) {
        setError({ code: 'history_failed', message: errorMessage(err), retryable: true });
      } finally {
        setLoadingHistory(false);
      }
    },
    [api],
  );

  // 恢复最近一次会话（create-or-resume）
  useEffect(() => {
    if (!active || sessionRef.current || sessions.length === 0) return;
    const latest = sessions[0];
    if (!latest) return;
    setCurrentSession(latest.session_id);
    void loadHistory(latest.session_id);
  }, [active, sessions, setCurrentSession, loadHistory]);

  const ensureSession = useCallback(async (): Promise<string | null> => {
    if (sessionRef.current) return sessionRef.current;
    try {
      const created = await api.createSession(title ?? null);
      setCurrentSession(created.session_id);
      setSessions((prev) => [created, ...prev]);
      return created.session_id;
    } catch (err) {
      setError({ code: 'session_failed', message: errorMessage(err), retryable: true });
      return null;
    }
  }, [api, title, setCurrentSession]);

  const applyEvent = useCallback((assistantId: string, event: AgentEvent) => {
    const patch = (update: (message: AgentMessage) => AgentMessage) =>
      setMessages((prev) => prev.map((m) => (m.id === assistantId ? update(m) : m)));

    switch (event.type) {
      case 'token':
        patch((m) => ({ ...m, content: m.content + event.data.text }));
        break;
      case 'tool_call_start':
        patch((m) => ({
          ...m,
          toolCalls: [
            ...(m.toolCalls ?? []),
            {
              id: nextAgentId('call'),
              tool: event.data.tool,
              arguments: event.data.arguments ?? {},
              status: 'running',
              startedAt: Date.now(),
            },
          ],
        }));
        break;
      case 'tool_call_result':
        patch((m) => ({ ...m, toolCalls: reduceToolCalls(m.toolCalls ?? [], event) }));
        break;
      case 'confirmation_required':
        setPending({
          token: event.data.token,
          tool: event.data.tool,
          arguments: event.data.arguments ?? {},
          expires_at: event.data.expires_at ?? null,
        });
        break;
      case 'error':
        setError({ code: event.data.code, message: event.data.message, retryable: true });
        break;
      case 'done':
        if (event.data.reason === 'budget_exhausted') {
          setNotice(event.data.message ?? '已达到本次会话的调用预算，已优雅终止。');
        }
        break;
    }
  }, [setPending]);

  const sendMessage = useCallback(
    (text: string, skill?: string | null) => {
      const trimmed = text.trim();
      if (!trimmed || streamingRef.current) return;
      const skillName = skill ?? null;
      lastRef.current = { text: trimmed, skill: skillName };

      void (async () => {
        const sid = await ensureSession();
        if (!sid) return;
        setError(null);
        setNotice(null);
        const assistantId = nextAgentId('assistant');
        setMessages((prev) => [
          ...prev,
          {
            id: nextAgentId('user'),
            role: 'user',
            content: trimmed,
            created_at: new Date().toISOString(),
          },
          { id: assistantId, role: 'assistant', content: '', created_at: null, streaming: true, toolCalls: [] },
        ]);
        streamingRef.current = true;
        setStreaming(true);
        const controller = new AbortController();
        abortRef.current = controller;
        try {
          for await (const event of stream({
            sessionId: sid,
            message: trimmed,
            skill: skillName,
            signal: controller.signal,
          })) {
            applyEvent(assistantId, event);
          }
        } catch (err) {
          setError({ code: 'stream_failed', message: errorMessage(err), retryable: true });
        } finally {
          setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, streaming: false } : m)));
          streamingRef.current = false;
          setStreaming(false);
          abortRef.current = null;
        }
      })();
    },
    [applyEvent, ensureSession, stream],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const confirm = useCallback(() => {
    const action = pendingRef.current;
    const sid = sessionRef.current;
    if (!action || !sid) return;
    void (async () => {
      setConfirming(true);
      try {
        await api.confirm(sid, action.token);
        setPending(null);
        setNotice(`已确认并执行：${action.tool}`);
        const resp = await api.getMessages(sid);
        setMessages(resp.items.map(toAgentMessage));
      } catch (err) {
        setError({ code: 'confirm_failed', message: errorMessage(err), retryable: true });
      } finally {
        setConfirming(false);
      }
    })();
  }, [api, setPending]);

  const cancelConfirmation = useCallback(() => setPending(null), [setPending]);

  const retry = useCallback(() => {
    const last = lastRef.current;
    if (last) sendMessage(last.text, last.skill);
  }, [sendMessage]);

  const selectSession = useCallback(
    (id: string) => {
      if (streamingRef.current || id === sessionRef.current) return;
      setCurrentSession(id);
      setError(null);
      setNotice(null);
      setPending(null);
      void loadHistory(id);
    },
    [loadHistory, setCurrentSession, setPending],
  );

  const newSession = useCallback(() => {
    if (streamingRef.current) return;
    setCurrentSession(null);
    setMessages([]);
    setError(null);
    setNotice(null);
    setPending(null);
  }, [setCurrentSession, setPending]);

  return {
    sessionId,
    sessions,
    messages,
    tools,
    skills,
    isStreaming,
    isConfirming,
    isLoadingHistory,
    error,
    notice,
    pendingAction,
    sendMessage,
    stop,
    confirm,
    cancelConfirmation,
    dismissError: () => setError(null),
    dismissNotice: () => setNotice(null),
    retry,
    selectSession,
    newSession,
  };
}
