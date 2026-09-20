/**
 * 角色 × 页面权限矩阵。
 *
 * 列集来自 `usePageRegistry`（后端注册表全集），不是前端常量数组——
 * 这正是原项目 `PAGE_MATRIX` 遗漏 `review` 缺陷的根治点。
 */

import { Button } from '@/components/ui/button';
import type { PageDescriptor } from '@/lib/pages';
import type { RoleOut } from '@/types';

export interface RoleMatrixProps {
  roles: RoleOut[];
  pages: PageDescriptor[];
  draft: Record<string, string[]>;
  isDirty: (role: string) => boolean;
  busy: boolean;
  onToggle: (role: string, key: string) => void;
  onSave: (role: string) => void;
  onReset: (role: string) => void;
  onDelete: (role: RoleOut) => void;
}

export function RoleMatrix({
  roles,
  pages,
  draft,
  isDirty,
  busy,
  onToggle,
  onSave,
  onReset,
  onDelete,
}: RoleMatrixProps) {
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full border-collapse text-sm">
        <thead className="bg-muted/40 text-muted-foreground">
          <tr>
            <th scope="col" className="bg-muted/40 sticky left-0 px-3 py-2 text-left font-medium">
              角色
            </th>
            {pages.map((page) => (
              <th
                key={page.key}
                scope="col"
                title={page.key}
                className="px-2 py-2 text-center font-medium whitespace-nowrap"
              >
                {page.label}
              </th>
            ))}
            <th scope="col" className="px-3 py-2 text-right font-medium whitespace-nowrap">
              操作
            </th>
          </tr>
        </thead>
        <tbody>
          {roles.map((role) => {
            const isAdminRole = role.name === 'admin';
            const selected = draft[role.name] ?? [];
            return (
              <tr key={role.name} className="border-t">
                <td className="bg-card sticky left-0 px-3 py-2">
                  <div className="font-medium">{role.label}</div>
                  <div className="text-muted-foreground text-xs">
                    {role.name}
                    {role.pages_configured ? '' : ' · 默认'}
                  </div>
                </td>
                {pages.map((page) => (
                  <td key={page.key} className="px-2 py-2 text-center">
                    <input
                      type="checkbox"
                      className="accent-primary size-4 align-middle"
                      aria-label={`${role.label} 可访问 ${page.label}`}
                      disabled={isAdminRole || busy}
                      checked={isAdminRole || selected.includes(page.key)}
                      onChange={() => onToggle(role.name, page.key)}
                    />
                  </td>
                ))}
                <td className="px-3 py-2 text-right whitespace-nowrap">
                  <div className="inline-flex gap-1">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={isAdminRole || busy || !isDirty(role.name)}
                      onClick={() => onSave(role.name)}
                    >
                      保存
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={isAdminRole || busy}
                      onClick={() => onReset(role.name)}
                    >
                      重置
                    </Button>
                    {!role.is_builtin && (
                      <Button
                        size="sm"
                        variant="destructive"
                        disabled={busy}
                        onClick={() => onDelete(role)}
                      >
                        删除
                      </Button>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
