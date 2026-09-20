/**
 * 行情读数据 hooks（总览页）。
 *
 * 情绪接口需 `overview` 页面权限；返回体携带 `stale` 新鲜度标记，
 * 由页面据此展示陈旧数据横幅。
 */

import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { marketApi } from '@/lib/api';
import type { SentimentHistoryResponse, SentimentResponse } from '@/types/market';

/** 行情域查询键工厂。 */
export const marketKeys = {
  sentiment: ['market', 'sentiment'] as const,
  sentimentHistory: (days: number) => ['market', 'sentiment', 'history', days] as const,
};

/** 最新（或指定日）情绪指标。 */
export function useSentimentQuery(): UseQueryResult<SentimentResponse> {
  return useQuery<SentimentResponse>({
    queryKey: marketKeys.sentiment,
    queryFn: ({ signal }) => marketApi.sentiment(signal),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

/** 最近 N 个交易日情绪（升序）。 */
export function useSentimentHistoryQuery(days = 20): UseQueryResult<SentimentHistoryResponse> {
  return useQuery<SentimentHistoryResponse>({
    queryKey: marketKeys.sentimentHistory(days),
    queryFn: ({ signal }) => marketApi.sentimentHistory(days, signal),
    staleTime: 60_000,
  });
}
