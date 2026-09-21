/**
 * 行情域契约（总览页所需子集）：情绪指标与历史。
 *
 * 逐字段镜像后端 `app/schemas/market.py` / `app/schemas/common.py`，
 * 字段名保持 snake_case。比率字段为**小数口径**（`0.42 = 42%`）。
 *
 * **注意**：本文件**不**从 `types/index.ts` 导出（该出口由其他 Task 维护），
 * 使用方一律 `import type { ... } from '@/types/market'`。
 */

/** 行情数据新鲜度标记（后端 `MarketMeta`）。 */
export interface MarketMeta {
  /** 数据是否已超出预期新鲜度窗口（上游不可用时的降级返回） */
  stale: boolean;
  /** 库中最新数据日期 */
  data_date: string | null;
}

/** 市场情绪指标。 */
export interface SentimentOut {
  trade_date: string;
  /** 情绪温度（0~100 分） */
  temperature: number;
  /** 情绪周期阶段（冰点 / 冰点转折 / 修复 / 加速/高潮 / 分歧 / 退潮）；未派生时为 null */
  stage: string | null;
  limit_up_count: number;
  limit_down_count: number;
  broken_board_count: number;
  /** 炸板率（小数口径） */
  broken_rate: number;
  up_count: number;
  down_count: number;
  max_continue_days: number | null;
  /** 昨日涨停今日平均溢价（小数口径） */
  premium_rate: number;
}

/** 某交易日情绪指标。 */
export interface SentimentResponse extends MarketMeta {
  trade_date: string | null;
  item: SentimentOut | null;
}

/** 最近 N 个交易日情绪（按交易日升序）。 */
export interface SentimentHistoryResponse extends MarketMeta {
  days: number;
  items: SentimentOut[];
}

// --------------------------------------------------------------- 通用外壳

/** 分页外壳字段（后端 `PageResponse` / `MarketPageResponse` 共有部分）。 */
export interface PageMeta {
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

/** 可选交易日列表。 */
export interface DatesResponse {
  dates: string[];
  limit: number;
}

// --------------------------------------------------------------- 涨停池

/** 关联板块（涨停原因所属题材）。 */
export interface PoolPlate {
  plate_id?: number | null;
  plate_name?: string | null;
}

/** 封板时间线单点（status: 1 封涨停 / 2 炸板 / 3 封跌停 / 4 开跌停）。 */
export interface PoolTimelinePoint {
  /** Unix 秒 */
  timestamp: number;
  status: number;
}

/** 涨停池成分（`turnover_rate` / `change_pct` / `seal_ratio` 小数口径）。 */
export interface PoolOut {
  trade_date: string;
  code: string;
  name: string;
  continue_days: number;
  limit_up_time: string | null;
  seal_amount_yuan: number | null;
  open_times: number | null;
  turnover_rate: number | null;
  amount_yuan: number | null;
  /** 总市值（元） */
  market_cap_yuan: number | null;
  /** 现价（元） */
  price: number | null;
  /** 涨跌幅（小数，0.1001 = +10.01%） */
  change_pct: number | null;
  /** 量比 */
  volume_bias_ratio: number | null;
  /** 流通市值（元） */
  free_cap_yuan: number | null;
  /** 封单比（小数） */
  seal_ratio: number | null;
  /** 涨停原因（上游口语化说明） */
  reason: string | null;
  /** 关联板块 */
  plates: PoolPlate[] | null;
  /** 封板时间线 */
  timeline: PoolTimelinePoint[] | null;
  /** 池型（对齐上游 pool_name，共 7 种） */
  pool_type: string;
}

/** 某交易日全部池型。 */
export interface PoolsResponse extends MarketMeta {
  trade_date: string | null;
  pools: Record<string, PoolOut[]>;
}

/** 某交易日单个池型成分。 */
export interface PoolResponse extends MarketMeta {
  trade_date: string | null;
  pool_type: string;
  min_continue_days: number;
  items: PoolOut[];
}

// --------------------------------------------------------------- 连板天梯

/** 天梯单元格：某票某交易日的连板情况。 */
export interface LadderCell {
  /** 该日连板天数 */
  boards: number;
  /** 该日首封时间 HH:MM */
  first_seal_time: string | null;
}

/** 天梯矩阵的一行（一只个股，跨多日的连板单元格）。 */
export interface LadderMatrixRow {
  code: string;
  name: string;
  /** 交易日（ISO 字符串）→ 该日单元格 */
  cells: Record<string, LadderCell>;
}

/** 连板天梯矩阵：列=交易日（升序），行=个股。 */
export interface LadderMatrixResponse extends MarketMeta {
  /** 交易日（ISO 字符串，升序） */
  days: string[];
  rows: LadderMatrixRow[];
  min_continue_days: number;
}

/** 连板天梯单行。 */
export interface LadderRowOut {
  trade_date: string;
  code: string;
  name: string;
  continue_days: number;
  first_seal_time: string | null;
}

/** 连板天梯分页响应。 */
export interface LadderPage extends PageMeta, MarketMeta {
  items: LadderRowOut[];
}

// --------------------------------------------------------------- 快讯

/** 7×24 快讯。 */
export interface NewsFlashOut {
  ts: string;
  level: string | null;
  title: string;
  summary: string | null;
  symbols: string[];
  categories: string[];
}

/** 快讯分页响应。 */
export interface NewsFlashPage extends PageMeta, MarketMeta {
  items: NewsFlashOut[];
}

// --------------------------------------------------------------- 主题

/** 主题强度榜单项（`core_avg_pct` 小数口径）。 */
export interface ThemeOut {
  trade_date: string;
  rank: number;
  name: string;
  core_avg_pct: number | null;
  description: string | null;
  core_count: number | null;
}

/** 某交易日主题强度榜。 */
export interface ThemesResponse extends MarketMeta {
  trade_date: string | null;
  items: ThemeOut[];
}

/** 主题成分股（`pct` / `turnover_rate` 小数口径）。 */
export interface ThemeStockOut {
  trade_date: string;
  theme_name: string;
  code: string;
  name: string;
  price: number;
  pct: number;
  turnover_rate: number;
  continue_days: number | null;
  selected_at: string | null;
}

/** 某日某主题的成分股。 */
export interface ThemeStocksResponse extends MarketMeta {
  trade_date: string;
  theme_name: string;
  items: ThemeStockOut[];
}

// --------------------------------------------------------------- 监管名单

/** 监管名单记录。 */
export interface MonitorStockOut {
  trade_date: string;
  kind: string;
  code: string;
  name: string;
  reason: string | null;
}

/** 某日监管名单。 */
export interface MonitorResponse extends MarketMeta {
  trade_date: string | null;
  kind: string | null;
  items: MonitorStockOut[];
}
