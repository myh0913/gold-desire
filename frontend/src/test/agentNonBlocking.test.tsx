/**
 * 非阻塞性：抽屉打开且流式进行中时，主应用不被遮罩禁用。
 *
 * 通过 stub 全局 fetch（元数据接口即时返回、对话流保持挂起）驱动真实抽屉。
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AgentDrawer } from '@/components/agent/AgentDrawer';

const encoder = new TextEncoder();

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

/** 永不结束的 SSE 响应：先给一帧 token，随后保持挂起（模拟模型仍在生成）。 */
function pendingSse(): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode('data: {"type":"token","data":{"text":"分析中"}}\n\n'));
    },
  });
  return { ok: true, status: 200, body: stream, text: async () => '' } as unknown as Response;
}

function stubFetch(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/chat')) return pendingSse();
      if (url.includes('/agent/tools') || url.includes('/agent/skills')) {
        return jsonResponse({ items: [] });
      }
      if (url.includes('/agent/sessions') && init?.method === 'POST') {
        return jsonResponse({ session_id: 's1', title: null, created_at: null });
      }
      return jsonResponse({ items: [] });
    }),
  );
}

describe('AgentDrawer 非阻塞性', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('流式进行中不遮蔽/禁用主应用', async () => {
    stubFetch();
    render(
      <div>
        <main data-testid="app-shell">主应用内容</main>
        <AgentDrawer open onOpenChange={() => {}} />
      </div>,
    );

    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());

    // 发起一轮对话，进入流式状态
    const textarea = screen.getByLabelText('Agent 输入框');
    fireEvent.change(textarea, { target: { value: '今天涨停结构如何' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '停止生成' })).toBeInTheDocument(),
    );

    // 主应用内容仍在文档中，未被 aria-hidden / inert 禁用
    const shell = screen.getByTestId('app-shell');
    expect(shell).toBeInTheDocument();
    expect(shell).not.toHaveAttribute('aria-hidden');
    expect(shell).not.toHaveAttribute('inert');

    // 抽屉外壳为指针穿透，仅面板自身可交互 → 遮罩不再拦截主应用点击/滚动
    expect(screen.getByTestId('agent-drawer-root').className).toContain('pointer-events-none');

    // 不锁定 body 滚动：主应用可继续滚动
    expect(document.body.style.overflow).not.toBe('hidden');

    // 不存在任何额外的阻塞式遮罩
    expect(document.querySelector('[data-blocking-overlay]')).toBeNull();
  });
});
