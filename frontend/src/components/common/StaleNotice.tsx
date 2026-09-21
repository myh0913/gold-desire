/**
 * 数据新鲜度提示：`stale` 时渲染琥珀色横幅（可关闭），否则不渲染。
 *
 * 输入为后端行情响应的 `MarketMeta`（`stale` / `data_date`）。
 * 总览页原有同款逻辑（AlertBanner variant="stale"），此处抽公共组件复用。
 */

import { useState } from 'react';
import { AlertBanner } from './AlertBanner';
import { fmtDate } from '@/lib/time';

export interface StaleNoticeProps {
  stale: boolean | undefined;
  dataDate: string | null | undefined;
  /** 数据名（如「情绪数据」「涨停池」），用于提示文案 */
  label?: string;
}

export function StaleNotice({ stale, dataDate, label = '数据' }: StaleNoticeProps) {
  const [dismissed, setDismissed] = useState(false);
  if (!stale || dismissed) return null;
  return (
    <AlertBanner
      variant="stale"
      message={`${label}可能不是最新：当前展示库中数据${
        dataDate ? `（${fmtDate(dataDate)}）` : ''
      }，上游恢复后将自动刷新。`}
      onDismiss={() => setDismissed(true)}
      autoHideMs={0}
    />
  );
}
