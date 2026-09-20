/**
 * 量化配置页数据 hooks（与页面同目录共置，不占用共享 `src/lib/queries.ts`）。
 *
 * 全部走 TanStack Query（typed），写操作成功后失效对应查询键，避免手工同步缓存。
 * 读接口需 `quantconfig` 页面权限；写接口 admin-only（服务端权威，前端隐藏仅为体验）。
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from '@tanstack/react-query';
import { configApi } from '@/lib/api';
import type {
  ConfigDiffOut,
  ConfigUpdate,
  ConfigVersionOut,
  ConfigVersionsResponse,
  DatasourcePingResponse,
  DatasourcePrefsResponse,
  DatasourceStateOut,
  DatasourcesResponse,
  FactorEffectivenessResponse,
  FactorsResponse,
  IngestHealthResponse,
  IngestJobsResponse,
  IngestTriggerOut,
  IngestTriggerRequest,
  StrategiesResponse,
  StrategyStateOut,
} from '@/types/config';

/** 配置域查询键工厂。 */
export const configKeys = {
  strategies: ['config', 'strategies'] as const,
  strategyVersions: (id: string) => ['config', 'strategies', id, 'versions'] as const,
  strategyDiff: (id: string, from: number, to: number) =>
    ['config', 'strategies', id, 'diff', from, to] as const,
  factors: ['config', 'factors'] as const,
  factorVersions: (id: string) => ['config', 'factors', id, 'versions'] as const,
  factorDiff: (id: string, from: number, to: number) =>
    ['config', 'factors', id, 'diff', from, to] as const,
  factorEffectiveness: (id: string) => ['config', 'factors', id, 'effectiveness'] as const,
};

/** 数据源 / 采集域查询键工厂。 */
export const datasourceKeys = {
  datasources: ['config', 'datasources'] as const,
  ingestJobs: ['config', 'ingest', 'jobs'] as const,
  ingestHealth: ['config', 'ingest', 'health'] as const,
};

// --------------------------------------------------------------- 策略

/** 策略列表（含 schema、生效参数与版本历史）。 */
export function useStrategiesQuery(): UseQueryResult<StrategiesResponse> {
  return useQuery<StrategiesResponse>({
    queryKey: configKeys.strategies,
    queryFn: ({ signal }) => configApi.strategies.list(signal),
  });
}

/** 保存并启用策略参数。 */
export function useSaveStrategyConfigMutation(strategyId: string) {
  const client = useQueryClient();
  return useMutation<ConfigVersionOut, Error, ConfigUpdate>({
    mutationFn: (body) => configApi.strategies.save(strategyId, body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: configKeys.strategies });
    },
  });
}

/** 启用 / 停用策略。 */
export function useSetStrategyEnabledMutation(strategyId: string) {
  const client = useQueryClient();
  return useMutation<StrategyStateOut, Error, boolean>({
    mutationFn: (enabled) =>
      enabled
        ? configApi.strategies.enable(strategyId)
        : configApi.strategies.disable(strategyId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: configKeys.strategies });
    },
  });
}

/** 策略参数版本历史。 */
export function useStrategyVersionsQuery(
  strategyId: string,
  enabled = true,
): UseQueryResult<ConfigVersionsResponse> {
  return useQuery<ConfigVersionsResponse>({
    queryKey: configKeys.strategyVersions(strategyId),
    queryFn: ({ signal }) => configApi.strategies.versions(strategyId, signal),
    enabled: enabled && strategyId !== '',
  });
}

/** 策略两版本参数差异。 */
export function useStrategyDiffQuery(
  strategyId: string,
  from: number | null,
  to: number | null,
): UseQueryResult<ConfigDiffOut> {
  return useQuery<ConfigDiffOut>({
    queryKey: configKeys.strategyDiff(strategyId, from ?? 0, to ?? 0),
    queryFn: ({ signal }) => configApi.strategies.diff(strategyId, from ?? 0, to ?? 0, signal),
    enabled: strategyId !== '' && from !== null && to !== null && from !== to,
  });
}

/** 回滚策略参数到历史版本。 */
export function useRollbackStrategyMutation(strategyId: string) {
  const client = useQueryClient();
  return useMutation<ConfigVersionOut, Error, number>({
    mutationFn: (version) => configApi.strategies.rollback(strategyId, version),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: configKeys.strategies });
      void client.invalidateQueries({ queryKey: configKeys.strategyVersions(strategyId) });
    },
  });
}

// --------------------------------------------------------------- 因子

/** 因子列表（含 schema 与生效参数）。 */
export function useFactorsQuery(): UseQueryResult<FactorsResponse> {
  return useQuery<FactorsResponse>({
    queryKey: configKeys.factors,
    queryFn: ({ signal }) => configApi.factors.list(signal),
  });
}

/** 保存并启用因子参数。 */
export function useSaveFactorConfigMutation(factorId: string) {
  const client = useQueryClient();
  return useMutation<ConfigVersionOut, Error, ConfigUpdate>({
    mutationFn: (body) => configApi.factors.save(factorId, body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: configKeys.factors });
    },
  });
}

/** 因子参数版本历史。 */
export function useFactorVersionsQuery(
  factorId: string,
  enabled = true,
): UseQueryResult<ConfigVersionsResponse> {
  return useQuery<ConfigVersionsResponse>({
    queryKey: configKeys.factorVersions(factorId),
    queryFn: ({ signal }) => configApi.factors.versions(factorId, signal),
    enabled: enabled && factorId !== '',
  });
}

/** 因子两版本参数差异。 */
export function useFactorDiffQuery(
  factorId: string,
  from: number | null,
  to: number | null,
): UseQueryResult<ConfigDiffOut> {
  return useQuery<ConfigDiffOut>({
    queryKey: configKeys.factorDiff(factorId, from ?? 0, to ?? 0),
    queryFn: ({ signal }) => configApi.factors.diff(factorId, from ?? 0, to ?? 0, signal),
    enabled: factorId !== '' && from !== null && to !== null && from !== to,
  });
}

/** 回滚因子参数到历史版本。 */
export function useRollbackFactorMutation(factorId: string) {
  const client = useQueryClient();
  return useMutation<ConfigVersionOut, Error, number>({
    mutationFn: (version) => configApi.factors.rollback(factorId, version),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: configKeys.factors });
      void client.invalidateQueries({ queryKey: configKeys.factorVersions(factorId) });
    },
  });
}

/** 因子有效性统计（按档位 + A/B/C 分段）。 */
export function useFactorEffectivenessQuery(
  factorId: string,
  enabled = true,
): UseQueryResult<FactorEffectivenessResponse> {
  return useQuery<FactorEffectivenessResponse>({
    queryKey: configKeys.factorEffectiveness(factorId),
    queryFn: ({ signal }) => configApi.factors.effectiveness(factorId, signal),
    enabled: enabled && factorId !== '',
  });
}

// --------------------------------------------------------------- 数据源 / 采集

/** 数据源注册表 + 能力取数顺序 + 健康度。 */
export function useDatasourcesQuery(): UseQueryResult<DatasourcesResponse> {
  return useQuery<DatasourcesResponse>({
    queryKey: datasourceKeys.datasources,
    queryFn: ({ signal }) => configApi.datasources.list(signal),
  });
}

/** 保存能力 → 有序源列表（主备切换，下一轮采集热生效）。 */
export function useSaveDatasourcePrefsMutation() {
  const client = useQueryClient();
  return useMutation<DatasourcePrefsResponse, Error, Record<string, string[]>>({
    mutationFn: (prefs) => configApi.datasources.savePrefs(prefs),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: datasourceKeys.datasources });
    },
  });
}

/** 连通性探测（`sources` 为空表示探测全部已注册源）。 */
export function usePingDatasourcesMutation() {
  const client = useQueryClient();
  return useMutation<DatasourcePingResponse, Error, string[]>({
    mutationFn: (sources) => configApi.datasources.ping(sources),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: datasourceKeys.datasources });
    },
  });
}

/** 启用 / 停用数据源。 */
export function useSetDatasourceEnabledMutation() {
  const client = useQueryClient();
  return useMutation<DatasourceStateOut, Error, { sourceId: string; enabled: boolean }>({
    mutationFn: ({ sourceId, enabled }) =>
      enabled
        ? configApi.datasources.enable(sourceId)
        : configApi.datasources.disable(sourceId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: datasourceKeys.datasources });
    },
  });
}

/** 最近采集任务明细。 */
export function useIngestJobsQuery(): UseQueryResult<IngestJobsResponse> {
  return useQuery<IngestJobsResponse>({
    queryKey: datasourceKeys.ingestJobs,
    queryFn: ({ signal }) => configApi.ingest.jobs(signal),
  });
}

/** 采集健康度汇总（各能力最近成功/失败与连续失败次数）。 */
export function useIngestHealthQuery(): UseQueryResult<IngestHealthResponse> {
  return useQuery<IngestHealthResponse>({
    queryKey: datasourceKeys.ingestHealth,
    queryFn: ({ signal }) => configApi.ingest.health(signal),
  });
}

/** 手动触发一次采集（admin）。 */
export function useTriggerIngestMutation() {
  const client = useQueryClient();
  return useMutation<IngestTriggerOut, Error, IngestTriggerRequest>({
    mutationFn: (body) => configApi.ingest.trigger(body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: datasourceKeys.ingestJobs });
      void client.invalidateQueries({ queryKey: datasourceKeys.ingestHealth });
    },
  });
}
