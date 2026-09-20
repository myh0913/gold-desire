/**
 * 会话编排：引导、登录/登出、全局 401 监听与页面级鉴权助手。
 *
 * - 引导：`useMeQuery` → 无 token 时先用 refresh cookie 换 token，再取 `/auth/me`。
 * - 401：`lib/api` 派发 `gold:unauthorized`，此处把会话缓存置空（路由守卫随即跳登录）。
 * - 页面级鉴权：`usePageAccess().hasPage(key)`，key 来自后端注册表下发的 `me.pages`。
 *   前端隐藏仅为体验，**服务端仍是权威**。
 */

import { useCallback, useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { UNAUTHORIZED_EVENT } from '@/lib/api';
import { queryKeys, useLoginMutation, useLogoutMutation, useMeQuery } from '@/lib/queries';
import { ROLE_ADMIN, type MeResponse } from '@/types';

export interface AuthState {
  me: MeResponse | null;
  /** 会话尚未确定（首屏引导中） */
  isLoading: boolean;
  isAuthenticated: boolean;
  login: ReturnType<typeof useLoginMutation>;
  logout: ReturnType<typeof useLogoutMutation>;
  refetch: () => void;
}

/** 当前会话与认证动作。 */
export function useAuth(): AuthState {
  const client = useQueryClient();
  const meQuery = useMeQuery();
  const login = useLoginMutation();
  const logout = useLogoutMutation();

  useEffect(() => {
    const handler = () => client.setQueryData(queryKeys.me, null);
    window.addEventListener(UNAUTHORIZED_EVENT, handler);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, handler);
  }, [client]);

  const refetch = useCallback(() => {
    void meQuery.refetch();
  }, [meQuery]);

  return {
    me: meQuery.data ?? null,
    isLoading: meQuery.isPending,
    isAuthenticated: meQuery.data != null,
    login,
    logout,
    refetch,
  };
}

export interface PageAccess {
  me: MeResponse | null;
  isLoading: boolean;
  isAdmin: boolean;
  /** 当前角色有效页面 key（后端注册表顺序） */
  pages: string[];
  /** 是否可访问该页面（admin 恒通过，服务端仍会二次校验） */
  hasPage: (key: string) => boolean;
}

/** 页面级鉴权助手。 */
export function usePageAccess(): PageAccess {
  const { me, isLoading } = useAuth();
  const pages = me?.pages ?? [];
  const isAdmin = me?.role === ROLE_ADMIN;

  return {
    me,
    isLoading,
    isAdmin,
    pages,
    hasPage: (key: string) => isAdmin || pages.includes(key),
  };
}
