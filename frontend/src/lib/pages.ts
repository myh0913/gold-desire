/**
 * 页面 key 工具：显示名覆盖 + 由后端角色数据派生完整页面注册表。
 *
 * 关键约束：**前端不维护页面清单**。页面 key 恒由后端
 * `app/core/pages.py` 注册表下发（`MeResponse.pages` / `RoleOut.pages`），
 * 本模块只做「显示名覆盖」与「派生」，故新增页面 key 不可能被静默丢弃。
 *
 * 原项目 `quant-web/src/pages/设置.tsx` 手工维护 `PAGE_MATRIX` 且遗漏 `review`，
 * 导致权限被静默丢弃——该缺陷在本设计中无法构造。
 */

import type { RoleOut } from '@/types';

/** 页面 key → 显示名覆盖表。**不是**权限矩阵，也不是页面清单。 */
const PAGE_LABELS: Record<string, string> = {
  overview: '总览',
  advice: '今日建议',
  review: '复盘',
  pools: '涨停池',
  ladder: '连板天梯',
  newsflash: '7×24 快讯',
  themes: '主题机会',
  monitor: '监管名单',
  quantconfig: '量化配置',
  settings: '设置',
  backtest: '回测',
};

/** 取页面显示名；未收录的 key 回退为 key 本身（保证新页面可见）。 */
export function pageLabel(key: string): string {
  return PAGE_LABELS[key] ?? key;
}

export interface PageDescriptor {
  key: string;
  label: string;
}

/**
 * 由角色列表派生**完整**页面 key 列表（注册表顺序）。
 *
 * admin 角色的 `pages` 恒等于后端注册表全集，故以其顺序为基准；
 * 其余角色中出现而 admin 未覆盖的 key 追加在后，任何 key 都不会被丢弃。
 */
export function derivePageKeys(roles: readonly RoleOut[]): string[] {
  const admin = roles.find((role) => role.name === 'admin');
  const ordered = admin ? [...admin.pages] : [];
  const seen = new Set(ordered);
  const extras: string[] = [];
  for (const role of roles) {
    for (const key of role.pages) {
      if (!seen.has(key)) {
        seen.add(key);
        extras.push(key);
      }
    }
  }
  return [...ordered, ...extras];
}

/** 派生带显示名的页面注册表（权限矩阵的行集）。 */
export function derivePageRegistry(roles: readonly RoleOut[]): PageDescriptor[] {
  return derivePageKeys(roles).map((key) => ({ key, label: pageLabel(key) }));
}
