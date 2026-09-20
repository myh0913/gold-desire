/**
 * 角色显示名：后端内置角色为 admin / analyst / viewer，自定义角色回退为 name 本身。
 */

const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  analyst: '分析师',
  viewer: '访客',
};

/** 取角色显示名；未收录的自定义角色回退为原始 name。 */
export function roleLabel(name: string): string {
  return ROLE_LABELS[name] ?? name;
}
