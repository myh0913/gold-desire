/**
 * 采集状态面板：最近任务明细 + 各能力健康度 + 手动触发（admin，二次确认）。
 *
 * 数据来自 `GET /api/ingest/jobs` 与 `GET /api/ingest/health`；
 * 触发调用 `POST /api/ingest/trigger`（body：`{ task, trade_date? }`）。
 */

import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { DataTable, type DataTableColumn } from '@/components/common/DataTable';
import { errorMessage } from '@/components/common/StateViews';
import { fmtDateTime } from '@/lib/time';
import type { CapabilityHealthOut, IngestJobOut } from '@/types/config';
import { useIngestHealthQuery, useIngestJobsQuery, useTriggerIngestMutation } from '../hooks';
import { ConfirmDialog } from '../components/ConfirmDialog';

export interface IngestStatusPanelProps {
  isAdmin: boolean;
}

const JOB_STATUS_VARIANT: Record<string, 'default' | 'secondary' | 'destructive'> = {
  success: 'default',
  ok: 'default',
  failed: 'destructive',
  error: 'destructive',
};

const JOB_COLUMNS: DataTableColumn<IngestJobOut>[] = [
  { key: 'capability', header: '能力', render: (row) => row.capability },
  { key: 'source', header: '数据源', render: (row) => row.source },
  {
    key: 'trade_date',
    header: '交易日',
    render: (row) => <span className="tabular-nums">{row.trade_date ?? '--'}</span>,
  },
  {
    key: 'status',
    header: '状态',
    render: (row) => (
      <Badge variant={JOB_STATUS_VARIANT[row.status] ?? 'secondary'}>{row.status}</Badge>
    ),
  },
  { key: 'rows', header: '行数', align: 'right', render: (row) => row.rows },
  { key: 'attempts', header: '尝试', align: 'right', render: (row) => row.attempts },
  {
    key: 'started_at',
    header: '开始时间',
    render: (row) => <span className="tabular-nums">{row.started_at ? fmtDateTime(row.started_at) : '--'}</span>,
  },
  {
    key: 'error',
    header: '错误',
    render: (row) =>
      row.error ? <span className="text-destructive break-all text-xs">{row.error}</span> : '--',
  },
];

const HEALTH_COLUMNS: DataTableColumn<[string, CapabilityHealthOut]>[] = [
  { key: 'capability', header: '能力', render: ([name]) => name },
  {
    key: 'last_success',
    header: '最近成功',
    render: ([, item]) => (
      <span className="tabular-nums">{item.last_success ? fmtDateTime(item.last_success) : '--'}</span>
    ),
  },
  {
    key: 'last_failure',
    header: '最近失败',
    render: ([, item]) => (
      <span className="tabular-nums">{item.last_failure ? fmtDateTime(item.last_failure) : '--'}</span>
    ),
  },
  {
    key: 'consecutive_failures',
    header: '连续失败',
    align: 'right',
    render: ([, item]) =>
      item.consecutive_failures > 0 ? (
        <span className="text-destructive font-medium">{item.consecutive_failures}</span>
      ) : (
        '0'
      ),
  },
];

export function IngestStatusPanel({ isAdmin }: IngestStatusPanelProps) {
  const jobs = useIngestJobsQuery();
  const health = useIngestHealthQuery();
  const trigger = useTriggerIngestMutation();

  const [task, setTask] = useState('');
  const [tradeDate, setTradeDate] = useState('');
  const [notice, setNotice] = useState('');
  const [confirming, setConfirming] = useState(false);

  const handleTrigger = () => {
    setNotice('');
    trigger.mutate(
      { task: task.trim(), trade_date: tradeDate.trim() || null },
      {
        onSuccess: (result) =>
          setNotice(
            `任务 ${result.task} 已执行：${result.status}（${result.rows} 行，来源 ${result.source}）`,
          ),
      },
    );
  };

  return (
    <div className="space-y-4">
      {isAdmin && (
        <div className="flex flex-wrap items-end gap-3 rounded-md border p-3">
          <div className="w-56 space-y-1.5">
            <label className="text-xs" htmlFor="ingest-task">
              采集任务名
            </label>
            <Input
              id="ingest-task"
              value={task}
              placeholder="如 daily_bars"
              onChange={(event) => setTask(event.target.value)}
            />
          </div>
          <div className="w-44 space-y-1.5">
            <label className="text-xs" htmlFor="ingest-date">
              目标交易日（缺省今天）
            </label>
            <Input
              id="ingest-date"
              type="date"
              value={tradeDate}
              onChange={(event) => setTradeDate(event.target.value)}
            />
          </div>
          <Button
            disabled={trigger.isPending || !task.trim()}
            onClick={() => setConfirming(true)}
          >
            {trigger.isPending ? '执行中…' : '手动触发采集'}
          </Button>
          <span className="text-muted-foreground text-xs">
            任务名须为后端采集注册表中的名称；未知任务名会返回 404。
          </span>
        </div>
      )}

      {notice && <p className="text-stock-down text-sm">{notice}</p>}
      {trigger.error && (
        <p role="alert" className="text-destructive text-sm">
          {errorMessage(trigger.error)}
        </p>
      )}

      <div className="space-y-2">
        <h3 className="text-sm font-medium">各能力采集健康度（近 7 天）</h3>
        <DataTable
          columns={HEALTH_COLUMNS}
          rows={Object.entries(health.data?.capabilities ?? {})}
          rowKey={([name]) => name}
          loading={health.isPending}
          error={health.error}
          onRetry={() => void health.refetch()}
          emptyTitle="暂无采集健康度"
        />
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-medium">最近采集任务</h3>
        <DataTable
          columns={JOB_COLUMNS}
          rows={jobs.data?.items}
          rowKey={(row) => row.job_id}
          loading={jobs.isPending}
          error={jobs.error}
          onRetry={() => void jobs.refetch()}
          emptyTitle="暂无采集任务记录"
        />
      </div>

      <ConfirmDialog
        open={confirming}
        title={`立即执行采集任务 ${task.trim()}？`}
        description="将同步执行一次采集并写入任务明细（幂等覆盖写）；指定历史交易日时按快照回放口径执行，不会回退到实时源。"
        confirmLabel="确认触发"
        busy={trigger.isPending}
        onCancel={() => setConfirming(false)}
        onConfirm={() => {
          setConfirming(false);
          handleTrigger();
        }}
      />
    </div>
  );
}
