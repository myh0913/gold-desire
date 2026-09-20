/**
 * TanStack Query 装配：共享 QueryClient、查询键与类型化 hooks。
 *
 * 重试策略：4xx（除 429）不重试——权限/校验类错误重试无意义；
 * 网络类错误最多重试 2 次。默认 staleTime 30s，避免切换页面即重新拉取。
 */

import { QueryClient } from '@tanstack/react-query';
import { ApiError } from './api';

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          if (error instanceof ApiError) {
            if (error.status === 401 || error.status === 403) return false;
            if (error.status >= 400 && error.status < 500 && error.status !== 429) return false;
          }
          return failureCount < 2;
        },
      },
      mutations: { retry: 0 },
    },
  });
}

/** 全局单例（`main.tsx` 注入 Provider）。 */
export const queryClient = createQueryClient();
