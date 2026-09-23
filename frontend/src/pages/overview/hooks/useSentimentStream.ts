/**
 * 总览实时流：订阅 `sentiment` / `cycle` / `pool` 频道，收到推送即失效对应查询（重拉 REST）。
 *
 * 同时收集 `alert` 频道消息供页面用 `AlertBanner` 展示。
 * WS 客户端为全局单例（断线退避 + 心跳 + 半开检测由 `lib/ws.ts` 负责）。
 */

import { useCallback, useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { getWsClient } from '@/lib/ws';

/** 总览页订阅的频道（`alert` 为仅管理员频道，由服务端按角色过滤）。 */
const CHANNELS = ['sentiment', 'cycle', 'pool'];

/** 行情域查询键前缀（覆盖情绪实时值与历史）。 */
const MARKET_KEY = ['market', 'sentiment'];

/** 情绪周期查询键（盘中每 10 分钟 judge，状态变化经 WS `cycle` 频道推送）。 */
const CYCLE_KEY = ['market', 'cycle'];

export interface SentimentStreamState {
  /** 最近收到的运维告警（最多保留 3 条） */
  alerts: string[];
  dismissAlert: (index: number) => void;
}

export function useSentimentStream(): SentimentStreamState {
  const client = getWsClient();
  const queryClient = useQueryClient();
  const [alerts, setAlerts] = useState<string[]>([]);

  useEffect(() => {
    client.connect();
    client.subscribe(CHANNELS);

    const unsubscribe = client.onMessage((message) => {
      if (message.type === 'sentiment' || message.type === 'pool') {
        void queryClient.invalidateQueries({ queryKey: MARKET_KEY });
        return;
      }
      if (message.type === 'cycle') {
        // 盘中情绪状态变化（T-0004）：失效周期查询，横幅/仓位因子实时刷新
        void queryClient.invalidateQueries({ queryKey: CYCLE_KEY });
        void queryClient.invalidateQueries({ queryKey: MARKET_KEY });
        return;
      }
      if (message.type === 'alert') {
        const payload = message.data as { message?: string } | undefined;
        setAlerts((previous) =>
          [payload?.message ?? '收到运维告警', ...previous].slice(0, 3),
        );
      }
    });

    return () => {
      unsubscribe();
      client.unsubscribe(CHANNELS);
    };
  }, [client, queryClient]);

  const dismissAlert = useCallback((index: number) => {
    setAlerts((previous) => previous.filter((_, position) => position !== index));
  }, []);

  return { alerts, dismissAlert };
}
