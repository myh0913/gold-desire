/**
 * WS 频道 → 查询失效：订阅频道，收到薄事件即失效指定查询键前缀并按需重取。
 *
 * 与旧 quant「30s 全量推送」相反：服务端只发元信息事件（不含 payload），
 * 前端收到后让 TanStack Query 失效对应查询，由 REST 重新拉取——带宽与
 * 数据量无关，且天然复用请求层缓存与鉴权。
 *
 * WS 客户端为全局单例（重连退避 / 自动重订阅由 `lib/ws.ts` 负责）；
 * 断线期间错过的事件由重连后的首次查询兜底。
 */

import { useEffect } from 'react';
import { useQueryClient, type QueryKey } from '@tanstack/react-query';
import { getWsClient } from '@/lib/ws';

export function useChannelRefresh(channels: readonly string[], queryKey: QueryKey): void {
  const client = getWsClient();
  const queryClient = useQueryClient();
  // 频道数组与键前缀按值稳定化，避免每次渲染重订阅。
  const channelKey = channels.join(',');

  useEffect(() => {
    const list = channelKey ? channelKey.split(',') : [];
    if (list.length === 0) return;
    client.connect();
    client.subscribe(list);
    const unsubscribe = client.onMessage((message) => {
      if ('data' in message && list.includes(message.type)) {
        void queryClient.invalidateQueries({ queryKey });
      }
    });
    return () => {
      unsubscribe();
      client.unsubscribe(list);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, queryClient, channelKey, JSON.stringify(queryKey)]);
}
