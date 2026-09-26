/**
 * 认证与授权契约。
 *
 * 逐字段镜像后端 `app/schemas/auth.py`（Pydantic v2）的响应/请求模型，
 * 字段名保持 snake_case 以免序列化时再做映射。
 */

/** 内置管理员角色名（后端 `services/role_service.py::BUILTIN_ROLES`）。 */
export const ROLE_ADMIN = 'admin';

/** 登录请求。 */
export interface LoginRequest {
  username: string;
  password: string;
}

/** 注册请求：图形验证码必填，邀请码可选（决定角色）。 */
export interface RegisterRequest {
  username: string;
  password: string;
  captcha_id: string;
  captcha: string;
  invite_code?: string | null;
}

/** 图形验证码响应：`image` 为 data URL，`captcha_id` 用于注册回传。 */
export interface CaptchaResponse {
  captcha_id: string;
  image: string;
}

/** 令牌响应：access token 走响应体，refresh 走 HttpOnly Cookie。 */
export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

/** 用户安全视图（不含 `password_hash`）。 */
export interface UserOut {
  id: number;
  username: string;
  role: string;
  enabled: boolean;
  /** 账户本金（元），仅本人可改；null 表示未设置。 */
  capital_yuan: number | null;
  last_login_at: string | null;
  created_at: string | null;
}

/** 更新本人资料：本金全量替换语义（null = 清除）。 */
export interface CapitalUpdate {
  capital_yuan?: number | null;
}

/** 当前登录者信息：用户 + 角色 + **由后端注册表派生**的有效页面 key。 */
export interface MeResponse {
  user: UserOut;
  role: string;
  pages: string[];
}

/** 管理员创建用户。 */
export interface UserCreate {
  username: string;
  password: string;
  role: string;
}

/** 管理员更新用户：角色与启用状态可单独或同时更新。 */
export interface UserUpdate {
  role?: string | null;
  enabled?: boolean | null;
}

/** 管理员重置用户密码。 */
export interface PasswordReset {
  password: string;
}

/** 角色视图：页面权限由后端注册表派生。 */
export interface RoleOut {
  name: string;
  label: string;
  is_builtin: boolean;
  /** 该角色的有效页面 key（注册表顺序） */
  pages: string[];
  /** 是否已自定义过（false 表示走注册表默认） */
  pages_configured: boolean;
}

/** 新建自定义角色。 */
export interface RoleCreate {
  name: string;
  label: string;
}

/** 更新角色页面权限：key 由服务端对照注册表校验，未知 key 一律拒绝。 */
export interface RolePagesUpdate {
  pages: string[];
}

/** 邀请码视图。 */
export interface InvitationOut {
  code: string;
  role: string;
  expires_at: string | null;
  used_by: string | null;
  used_at: string | null;
  revoked: boolean;
  created_at: string | null;
}

/** 创建邀请码：有效期 1~365 天。 */
export interface InvitationCreate {
  role: string;
  expires_in_days: number;
}
