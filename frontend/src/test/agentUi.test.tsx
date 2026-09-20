/**
 * Agent 抽屉 UI 组件：工具调用卡片（denied 区分）、输入区（流式禁用 / Enter 发送）、
 * HITL 确认弹窗（必须显式确认）、工具清单（非 admin 无变更入口）与流式光标。
 */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { AgentComposer } from '@/components/agent/AgentComposer';
import { AgentConfirmDialog } from '@/components/agent/AgentConfirmDialog';
import { AgentMessageItem } from '@/components/agent/AgentMessageItem';
import { AgentToolCallCard } from '@/components/agent/AgentToolCallCard';
import { AgentToolList } from '@/components/agent/AgentToolList';
import type { AgentToolCall, AgentToolInfo } from '@/types/agent';

const call = (overrides: Partial<AgentToolCall>): AgentToolCall => ({
  id: 'c1',
  tool: 'query_stocks',
  arguments: { q: '600' },
  status: 'ok',
  startedAt: 0,
  ...overrides,
});

describe('AgentToolCallCard', () => {
  it('denied 状态显著区分，且可展开查看参数', () => {
    const { unmount } = render(<AgentToolCallCard call={call({ status: 'denied', summary: '无权调用工具' })} />);
    const denied = screen.getByTestId('tool-call-card');
    expect(denied).toHaveAttribute('data-status', 'denied');
    expect(denied).toHaveTextContent('已拒绝');

    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByText(/"q": "600"/)).toBeInTheDocument();
    expect(screen.getByText('无权调用工具')).toBeInTheDocument();
    unmount();

    render(<AgentToolCallCard call={call({})} />);
    expect(screen.getByTestId('tool-call-card')).toHaveAttribute('data-status', 'ok');
    expect(screen.getByTestId('tool-call-card')).toHaveTextContent('成功');
  });
});

describe('AgentComposer', () => {
  it('Enter 发送、Shift+Enter 换行', () => {
    const onSend = vi.fn();
    render(<AgentComposer onSend={onSend} onStop={vi.fn()} isStreaming={false} />);
    const textarea = screen.getByLabelText('Agent 输入框');

    fireEvent.change(textarea, { target: { value: '你好' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('你好');

    onSend.mockClear();
    fireEvent.change(textarea, { target: { value: '换行' } });
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it('流式期间禁用输入并切换为停止按钮', () => {
    const onStop = vi.fn();
    const { rerender } = render(
      <AgentComposer onSend={vi.fn()} onStop={onStop} isStreaming={false} />,
    );
    expect(screen.getByRole('button', { name: '发送' })).toBeInTheDocument();

    rerender(<AgentComposer onSend={vi.fn()} onStop={onStop} isStreaming />);
    expect(screen.getByLabelText('Agent 输入框')).toBeDisabled();
    expect(screen.queryByRole('button', { name: '发送' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '停止生成' }));
    expect(onStop).toHaveBeenCalledTimes(1);
  });
});

describe('AgentConfirmDialog', () => {
  const action = {
    token: 'tok-1',
    tool: 'delete_strategy',
    arguments: { id: 'S4' },
    expires_at: '2026-09-19T02:00:00Z',
  };

  it('必须显式确认，绝不自动执行', () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <AgentConfirmDialog
        action={action}
        isConfirming={false}
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );

    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    expect(screen.getByText('delete_strategy')).toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '确认执行' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('无待办时不渲染', () => {
    render(
      <AgentConfirmDialog
        action={null}
        isConfirming={false}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });
});

describe('AgentToolList', () => {
  const readOnly: AgentToolInfo = {
    name: 'query_stocks',
    description: '检索股票',
    mutating: false,
    requires_confirmation: false,
    required_role: null,
  };

  it('非 admin（清单无变更工具）不渲染任何变更入口', () => {
    const { rerender } = render(<AgentToolList tools={[readOnly]} />);
    expect(screen.getByText('query_stocks')).toBeInTheDocument();
    expect(screen.queryByTestId('agent-mutating-tool')).toBeNull();
    expect(screen.queryByText('变更')).toBeNull();

    rerender(
      <AgentToolList
        tools={[
          readOnly,
          { ...readOnly, name: 'delete_strategy', mutating: true, requires_confirmation: true, required_role: 'admin' },
        ]}
      />,
    );
    expect(screen.getByTestId('agent-mutating-tool')).toBeInTheDocument();
  });
});

describe('AgentMessageItem', () => {
  it('流式中渲染光标，结束后移除', () => {
    const { rerender } = render(
      <AgentMessageItem
        message={{ id: 'a1', role: 'assistant', content: '分析中', created_at: null, streaming: true }}
      />,
    );
    expect(screen.getByTestId('stream-caret')).toBeInTheDocument();

    rerender(
      <AgentMessageItem
        message={{ id: 'a1', role: 'assistant', content: '分析完成', created_at: null, streaming: false }}
      />,
    );
    expect(screen.queryByTestId('stream-caret')).toBeNull();
  });
});
