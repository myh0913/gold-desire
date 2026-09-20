/**
 * 页面注册表：**从后端角色数据派生**，不硬编码页面清单。
 *
 * `GET /api/admin/roles` 返回的每个角色都带 `pages`（由后端 `core/pages.py`
 * 注册表派生，admin 恒为全集），故行集 = 后端注册表全集。
 * 新增页面 key 会自动出现在矩阵中，无法被静默丢弃。
 */

import { useMemo } from 'react';
import { derivePageRegistry, type PageDescriptor } from '@/lib/pages';
import { useRolesQuery } from '@/lib/queries';
import type { RoleOut } from '@/types';

export interface PageRegistryState {
  roles: RoleOut[];
  /** 权限矩阵的列（后端注册表全集，注册表顺序） */
  pages: PageDescriptor[];
  loading: boolean;
  error: unknown;
  refetch: () => void;
}

export function usePageRegistry(enabled = true): PageRegistryState {
  const query = useRolesQuery(enabled);
  const roles = useMemo(() => query.data ?? [], [query.data]);

  const pages = useMemo(() => derivePageRegistry(roles), [roles]);

  return {
    roles,
    pages,
    loading: query.isPending,
    error: query.error,
    refetch: () => {
      void query.refetch();
    },
  };
}
