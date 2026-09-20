/**
 * 应用挂载前缀（与 vite `base` / 反向代理子路径保持一致），例如 `'/gd'`。
 *
 * 生产构建由 vite 注入 `import.meta.env.BASE_URL`（带尾斜杠，如 `'/gd/'`）；
 * 本地 dev（未设 base）时空字符串。去掉尾斜杠，便于直接拼接 `'/api'`、`'/ws'`。
 *
 * 存在的原因：vite 的 `base` 只重写 HTML/静态资源引用，
 * **不会**影响 `fetch()` / `new WebSocket()` 里的绝对路径，也不会影响
 * React Router 的 basename —— 这三处都要显式带上本前缀。
 */
export const APP_BASE = ((import.meta.env.BASE_URL as string | undefined) ?? '/').replace(
  /\/+$/,
  '',
);
