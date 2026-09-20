/**
 * 数据 hooks：认证 + 管理端（用户/角色/邀请码）。
 *
 * - 会话引导（`useMeQuery`）：无 access token 时先用 refresh cookie 换新 token，
 *   再取 `/auth/me`；未登录返回 `null` 而非抛错，便于路由守卫区分「加载中 / 未登录」。
 * - 所有 mutation 在成功后失效对应查询键，避免手工同步缓存。
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from '@tanstack/react-query';
import {
  api,
  ApiError,
} from './api';
import { clearAccessToken, getAccessToken, setAccessToken } from './tokenStore';
import type {
  CaptchaResponse,
  InvitationCreate,
  InvitationOut,
  LoginRequest,
  MeResponse,
  PasswordReset,
  RegisterRequest,
  RoleCreate,
  RoleOut,
  RolePagesUpdate,
  TokenResponse,
  UserCreate,
  UserOut,
  UserUpdate,
} from '@/types';

/** 查询键工厂。 */
export const queryKeys = {
  me: ['auth', 'me'] as const,
  captcha: ['auth', 'captcha'] as const,
  health: ['system', 'health'] as const,
  users: ['admin', 'users'] as const,
  roles: ['admin', 'roles'] as const,
  invitations: ['admin', 'invitations'] as const,
};

/** 引导会话：refresh cookie → access token → /auth/me。未登录返回 null。 */
export async function fetchSession(signal?: AbortSignal): Promise<MeResponse | null> {
  if (!getAccessToken()) {
    try {
      const token = await api.auth.refresh();
      setAccessToken(token.access_token);
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) return null;
      throw error;
    }
  }
  try {
    return await api.auth.me(signal);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      clearAccessToken();
      return null;
    }
    throw error;
  }
}

/** 当前会话（未登录为 null）。 */
export function useMeQuery(): UseQueryResult<MeResponse | null> {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: ({ signal }) => fetchSession(signal),
    retry: false,
    staleTime: 5 * 60_000,
  });
}

/** 登录：成功后写入 access token 并刷新会话。 */
export function useLoginMutation() {
  const client = useQueryClient();
  return useMutation<TokenResponse, Error, LoginRequest>({
    mutationFn: (body) => api.auth.login(body),
    onSuccess: (token) => {
      setAccessToken(token.access_token);
      void client.invalidateQueries({ queryKey: queryKeys.me });
    },
  });
}

/** 登出：无论服务端成败都清空本地凭证。 */
export function useLogoutMutation() {
  const client = useQueryClient();
  return useMutation<void, Error, void>({
    mutationFn: () => api.auth.logout(),
    onSettled: () => {
      clearAccessToken();
      client.setQueryData(queryKeys.me, null);
      client.removeQueries({ queryKey: queryKeys.users });
      client.removeQueries({ queryKey: queryKeys.roles });
      client.removeQueries({ queryKey: queryKeys.invitations });
    },
  });
}

/** 注册：图形验证码必填，邀请码可选。 */
export function useRegisterMutation() {
  const client = useQueryClient();
  return useMutation<UserOut, Error, RegisterRequest>({
    mutationFn: (body) => api.auth.register(body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.users });
    },
  });
}

/** 图形验证码（手动触发：`refetch()` 换一张）。 */
export function useCaptchaQuery(enabled: boolean) {
  return useQuery<CaptchaResponse>({
    queryKey: queryKeys.captcha,
    queryFn: ({ signal }) => api.auth.captcha(signal),
    enabled,
    staleTime: 0,
    gcTime: 0,
  });
}

// ===== 管理端：用户 =====

export function useUsersQuery(enabled = true) {
  return useQuery<UserOut[]>({
    queryKey: queryKeys.users,
    queryFn: ({ signal }) => api.admin.users.list(signal),
    enabled,
  });
}

export function useCreateUserMutation() {
  const client = useQueryClient();
  return useMutation<UserOut, Error, UserCreate>({
    mutationFn: (body) => api.admin.users.create(body),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.users }),
  });
}

export function useUpdateUserMutation() {
  const client = useQueryClient();
  return useMutation<UserOut, Error, { username: string; body: UserUpdate }>({
    mutationFn: ({ username, body }) => api.admin.users.update(username, body),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.users }),
  });
}

export function useResetPasswordMutation() {
  const client = useQueryClient();
  return useMutation<UserOut, Error, { username: string; body: PasswordReset }>({
    mutationFn: ({ username, body }) => api.admin.users.resetPassword(username, body),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.users }),
  });
}

export function useDeleteUserMutation() {
  const client = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (username) => api.admin.users.remove(username),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.users }),
  });
}

// ===== 管理端：角色 =====

export function useRolesQuery(enabled = true) {
  return useQuery<RoleOut[]>({
    queryKey: queryKeys.roles,
    queryFn: ({ signal }) => api.admin.roles.list(signal),
    enabled,
  });
}

export function useCreateRoleMutation() {
  const client = useQueryClient();
  return useMutation<RoleOut, Error, RoleCreate>({
    mutationFn: (body) => api.admin.roles.create(body),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.roles }),
  });
}

export function useDeleteRoleMutation() {
  const client = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (name) => api.admin.roles.remove(name),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.roles }),
  });
}

export function useSetRolePagesMutation() {
  const client = useQueryClient();
  return useMutation<RoleOut, Error, { name: string; body: RolePagesUpdate }>({
    mutationFn: ({ name, body }) => api.admin.roles.setPages(name, body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.roles });
      void client.invalidateQueries({ queryKey: queryKeys.me });
    },
  });
}

export function useResetRolePagesMutation() {
  const client = useQueryClient();
  return useMutation<RoleOut, Error, string>({
    mutationFn: (name) => api.admin.roles.resetPages(name),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.roles });
      void client.invalidateQueries({ queryKey: queryKeys.me });
    },
  });
}

// ===== 管理端：邀请码 =====

export function useInvitationsQuery(enabled = true) {
  return useQuery<InvitationOut[]>({
    queryKey: queryKeys.invitations,
    queryFn: ({ signal }) => api.admin.invitations.list(signal),
    enabled,
  });
}

export function useCreateInvitationMutation() {
  const client = useQueryClient();
  return useMutation<InvitationOut, Error, InvitationCreate>({
    mutationFn: (body) => api.admin.invitations.create(body),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.invitations }),
  });
}

export function useRevokeInvitationMutation() {
  const client = useQueryClient();
  return useMutation<InvitationOut, Error, string>({
    mutationFn: (code) => api.admin.invitations.revoke(code),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.invitations }),
  });
}
