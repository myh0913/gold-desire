/**
 * WebSocket 协议契约。
 *
 * 采用「控制消息 + 频道消息」两段式：
 *
 * - 控制消息（`hello` / `heartbeat`）由客户端内部消费；
 * - 频道消息（`advice` / `ingest` / `alert` / ...）的 `data` 由订阅方按频道解析，
 *   避免把尚未定稿的业务模型钉死在传输层。
 *
 * 后端在上游故障/限频时回旧值并置 `_stale: true`，前端据此展示 stale 横幅。
 */

/** 连接状态。 */
export type WsStatus = 'connecting' | 'open' | 'closed';

/** 业务频道名。 */
export type WsChannel =
  | 'advice'
  | 'ingest'
  | 'alert'
  | 'sentiment'
  | 'cycle'
  | 'pool'
  | 'newsflash'
  | 'themes';

/** 服务端控制消息。 */
export type WsControlMessage =
  | { type: 'hello'; message: string }
  | { type: 'heartbeat'; ts: number };

/** 频道消息信封：`data` 由订阅方按 `type` 解析。 */
export interface WsChannelMessage {
  type: WsChannel;
  data: unknown;
  /** 后端标记的陈旧数据（上游不可用时的降级返回） */
  _stale?: boolean;
  ts?: number;
}

/** 服务端下行消息。 */
export type WsInbound = WsControlMessage | WsChannelMessage;

/** 客户端上行消息。 */
export type WsOutbound =
  | { type: 'subscribe'; channels: string[] }
  | { type: 'unsubscribe'; channels: string[] }
  | { type: 'ping' };

/** 服务端告警：上游接口故障 / 采集任务失败。 */
export interface WsAlert {
  /** 故障来源，如 `sentiment` / `ingest:daily_bars` */
  source: string;
  /** 人话描述（已截断） */
  message: string;
  /** 广播时间 Unix 毫秒 */
  ts: number;
}
