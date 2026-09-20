/**
 * 通用 API 契约：错误信封与分页。
 *
 * 与后端 `app/core/errors.py::_envelope` / 列表接口形状保持一致。
 */

/** 后端统一错误信封。 */
export interface ApiErrorBody {
  error: {
    /** 稳定的机器可读错误码，如 `unauthorized` / `page_forbidden` */
    code: string;
    /** 面向用户的中文信息 */
    message: string;
    /** 附加结构化上下文（页面 key、未知 key 列表等） */
    detail?: unknown;
  };
}

/** 分页结果（后端列表接口统一形状）。 */
export interface PageResult<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/** 分页请求参数（后端有默认与最大页大小限制）。 */
export interface PageParams {
  page?: number;
  page_size?: number;
}

/** 存活探针响应（`GET /api/health`）。 */
export interface HealthResponse {
  status: string;
  service: string;
  version?: string;
}
