/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 后端 API 基础地址；缺省 `/api`（走 Vite 代理） */
  readonly VITE_API_BASE?: string;
  /** WebSocket 地址；缺省按当前页面协议/主机推导 `/ws` */
  readonly VITE_WS_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
