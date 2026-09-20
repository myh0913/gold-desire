/**
 * 角色权限草稿：本地勾选状态 + 与后端值比对（dirty）。
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import type { RoleOut } from '@/types';

export interface RolesDraftState {
  draft: Record<string, string[]>;
  isDirty: (role: string) => boolean;
  toggle: (role: string, key: string) => void;
  /** 丢弃本地改动，回到后端值 */
  discard: () => void;
}

function toDraft(roles: readonly RoleOut[]): Record<string, string[]> {
  return Object.fromEntries(roles.map((role) => [role.name, [...role.pages]]));
}

export function useRolesDraft(roles: readonly RoleOut[]): RolesDraftState {
  const [draft, setDraft] = useState<Record<string, string[]>>(() => toDraft(roles));

  useEffect(() => {
    setDraft(toDraft(roles));
  }, [roles]);

  const toggle = useCallback((role: string, key: string) => {
    setDraft((current) => {
      const selected = current[role] ?? [];
      const next = selected.includes(key)
        ? selected.filter((item) => item !== key)
        : [...selected, key];
      return { ...current, [role]: next };
    });
  }, []);

  const baseline = useMemo(() => toDraft(roles), [roles]);

  const isDirty = useCallback(
    (role: string) => {
      const current = [...(draft[role] ?? [])].sort();
      const original = [...(baseline[role] ?? [])].sort();
      return current.length !== original.length || current.some((key, i) => key !== original[i]);
    },
    [draft, baseline],
  );

  const discard = useCallback(() => setDraft(baseline), [baseline]);

  return { draft, isDirty, toggle, discard };
}
