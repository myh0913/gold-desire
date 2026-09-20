/**
 * 量化配置与运维契约：策略 / 因子 / 数据源 / 采集。
 *
 * 逐字段镜像后端 `app/schemas/config.py`（Pydantic v2），字段名保持 snake_case。
 *
 * **注意**：本文件**不**从 `types/index.ts` 导出（该出口由其他 Task 维护），
 * 使用方一律 `import type { ... } from '@/types/config'`。
 */

/** 参数类型（后端 `factors.base.ParamType`）。 */
export type ParamType = 'float' | 'int' | 'bool' | 'percent' | 'enum';

/**
 * 单个参数声明（后端 `FactorParamSpec.to_dict()` / `StrategyParamSpec`，两者同形）。
 *
 * `percent` 为**小数口径**（`0.08 = 8%`），`min` / `max` 亦为小数口径。
 */
export interface ParamSpec {
  key: string;
  label?: string;
  type: ParamType;
  default?: unknown;
  min?: number | null;
  max?: number | null;
  step?: number | null;
  unit?: string | null;
  description?: string;
}

/** 档位声明（后端 `Bucket.to_dict()`）。 */
export interface BucketDef {
  label: string;
  lo?: number | null;
  hi?: number | null;
  lo_inclusive?: boolean;
  hi_inclusive?: boolean;
  equals?: string | null;
}

/**
 * 参数 schema：策略为 `{ params }`，因子额外含 `buckets`。
 *
 * 后端**未**为 `enum` 参数下发候选值，故 enum 候选取自 `buckets[].equals`。
 */
export interface ParamsSchema {
  params?: ParamSpec[];
  buckets?: BucketDef[];
}

// --------------------------------------------------------------- 版本化配置

/** 一个参数配置版本。 */
export interface ConfigVersionOut {
  owner_id: string;
  version: number;
  status: string;
  params: Record<string, unknown>;
  note: string | null;
  created_by: string | null;
  created_at: string | null;
}

/** 某归属（策略/因子）的版本历史。 */
export interface ConfigVersionsResponse {
  owner_id: string;
  items: ConfigVersionOut[];
}

/** 两个版本之间的参数差异；`changed` 为 `[旧值, 新值]`。 */
export interface ConfigDiffOut {
  owner_id: string;
  from_version: number;
  to_version: number;
  added: Record<string, unknown>;
  removed: Record<string, unknown>;
  changed: Record<string, [unknown, unknown]>;
}

/** 保存参数请求体（整包覆盖）。 */
export interface ConfigUpdate {
  params: Record<string, unknown>;
  note?: string | null;
}

// --------------------------------------------------------------- 策略 / 因子

/** 策略定义 + 参数 schema + 生效参数 + 版本历史。 */
export interface StrategyOut {
  strategy_id: string;
  label: string;
  version: string;
  description: string | null;
  enabled: boolean;
  phases: string[];
  gate_matrix: Record<string, { allowed: boolean; position_factor: number }>;
  params_schema: ParamsSchema;
  active_version: number | null;
  active_params: Record<string, unknown>;
  versions: ConfigVersionOut[];
}

/** 策略列表。 */
export interface StrategiesResponse {
  items: StrategyOut[];
}

/** 策略启停结果。 */
export interface StrategyStateOut {
  strategy_id: string;
  enabled: boolean;
}

/** 因子定义 + 参数 schema + 生效参数。 */
export interface FactorOut {
  factor_id: string;
  label: string;
  category: string;
  description: string | null;
  enabled: boolean;
  params_schema: ParamsSchema;
  buckets: BucketDef[];
  active_version: number | null;
  active_params: Record<string, unknown>;
}

/** 因子列表。 */
export interface FactorsResponse {
  items: FactorOut[];
}

/** 单段（A/B/C）统计。 */
export interface BucketSegmentOut {
  n: number;
  mean_return: number;
  win_rate: number;
}

/** 单档位统计（含 A/B/C 分段，**绝不只报聚合**）。 */
export interface BucketStatsOut {
  bucket: string;
  n: number;
  mean_return: number;
  win_rate: number;
  segments: Record<string, BucketSegmentOut>;
}

/** 因子有效性统计。 */
export interface FactorEffectivenessResponse {
  factor_id: string;
  params_version: string;
  cuts: [string, string] | null;
  sample_count: number;
  buckets: BucketStatsOut[];
}

// --------------------------------------------------------------- 数据源 / 采集

/** 单能力健康度快照（`health[capability]`）。 */
export interface DatasourceHealthItem {
  ok?: boolean;
  latency_ms?: number | null;
  detail?: unknown;
}

/** 数据源注册信息 + 最新健康度。 */
export interface DatasourceOut {
  source_id: string;
  label: string;
  kind: string;
  capabilities: string[];
  rate_limit_per_min: number;
  enabled: boolean;
  priority: number;
  health: Record<string, DatasourceHealthItem>;
  last_check: string | null;
}

/** 数据源注册表 + 能力取数顺序 + 健康度。 */
export interface DatasourcesResponse {
  items: DatasourceOut[];
  capability_order: Record<string, string[]>;
}

/** 保存后的能力取数顺序。 */
export interface DatasourcePrefsResponse {
  prefs: Record<string, string[]>;
}

/** 数据源启停结果。 */
export interface DatasourceStateOut {
  source_id: string;
  enabled: boolean;
}

/** 连通性探测结果：`source_id -> { capability: { ok, latency_ms, error } }`。 */
export interface DatasourcePingResponse {
  results: Record<string, Record<string, { ok: boolean; latency_ms: number | null; error: string | null }>>;
}

/** 采集任务执行明细。 */
export interface IngestJobOut {
  job_id: string;
  capability: string;
  source: string;
  trade_date: string | null;
  status: string;
  rows: number;
  attempts: number;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

/** 最近采集任务列表。 */
export interface IngestJobsResponse {
  limit: number;
  items: IngestJobOut[];
}

/** 单能力采集健康度。 */
export interface CapabilityHealthOut {
  last_success: string | null;
  last_failure: string | null;
  consecutive_failures: number;
}

/** 采集健康度汇总。 */
export interface IngestHealthResponse {
  since: string;
  capabilities: Record<string, CapabilityHealthOut>;
}

/** 手动触发采集请求。 */
export interface IngestTriggerRequest {
  task: string;
  trade_date?: string | null;
}

/** 手动触发一次采集的结果。 */
export interface IngestTriggerOut {
  task: string;
  capability: string;
  trade_date: string;
  status: string;
  rows: number;
  source: string;
  job_id: string;
  error: string | null;
  attempts: number;
  duration_ms: number;
}
