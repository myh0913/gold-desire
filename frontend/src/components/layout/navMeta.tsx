/**
 * 导航项构造：把后端下发的页面 key 映射为可渲染的导航项。
 *
 * 这里只提供**展示元数据**（路由路径 / 图标 / 是否精确匹配），
 * 页面清单恒来自后端（`me.pages`）。未在 `NAV_META` 中登记的 key
 * 仍会出现在导航里（回退路径 `/{key}`、回退图标），因此新增页面 key
 * 不可能被前端静默丢弃；尚未实现路由的 key 标记为 `available: false` 只读展示。
 */

import {
  Boxes,
  Compass,
  Flame,
  History,
  LayoutDashboard,
  Lightbulb,
  ListChecks,
  Settings as SettingsIcon,
  ShieldAlert,
  SlidersHorizontal,
  TrendingUp,
  Zap,
  type LucideIcon,
} from 'lucide-react';
import { pageLabel } from '@/lib/pages';

export interface NavEntry {
  key: string;
  path: string;
  label: string;
  icon: LucideIcon;
  /** 是否精确匹配（`/` 需要） */
  end: boolean;
  /** 路由是否已实现（未实现的仅展示，不可点击） */
  available: boolean;
}

interface NavMeta {
  path: string;
  icon: LucideIcon;
  end?: boolean;
}

/** 已实现路由的页面：key → 展示元数据。 */
const NAV_META: Record<string, NavMeta> = {
  overview: { path: '/', icon: LayoutDashboard, end: true },
  settings: { path: '/settings', icon: SettingsIcon },
  advice: { path: '/advice', icon: Lightbulb },
  review: { path: '/review', icon: TrendingUp },
  pools: { path: '/pools', icon: ListChecks },
  ladder: { path: '/ladder', icon: Flame },
  newsflash: { path: '/newsflash', icon: Zap },
  themes: { path: '/themes', icon: Compass },
  monitor: { path: '/monitor', icon: ShieldAlert },
  quantconfig: { path: '/quantconfig', icon: SlidersHorizontal },
  backtest: { path: '/backtest', icon: History },
};

/** 由后端页面 key 列表构造导航项（顺序即注册表顺序）。 */
export function buildNav(pages: readonly string[]): NavEntry[] {
  return pages.map((key) => {
    const meta = NAV_META[key];
    return {
      key,
      path: meta?.path ?? `/${key}`,
      label: pageLabel(key),
      icon: meta?.icon ?? Boxes,
      end: meta?.end ?? false,
      available: meta !== undefined,
    };
  });
}

/** 导航分组元数据：组顺序即展示顺序；key 不在其中的页面进「其他」兜底组。 */
const NAV_GROUPS: { label: string; keys: readonly string[] }[] = [
  { label: '行情感知', keys: ['overview', 'pools', 'ladder', 'themes', 'newsflash', 'monitor'] },
  { label: '决策', keys: ['advice', 'review', 'backtest'] },
  { label: '系统', keys: ['quantconfig', 'settings'] },
];

/** 渲染用导航分组（仅保留有页面的组；兜底组保证未登记 key 不被静默丢弃）。 */
export interface NavGroup {
  key: string;
  label: string;
  entries: NavEntry[];
}

/** 由后端页面 key 列表构造分组导航项。 */
export function buildNavGroups(pages: readonly string[]): NavGroup[] {
  const entries = buildNav(pages);
  const byKey = new Map(entries.map((entry) => [entry.key, entry]));
  const grouped = new Set<string>();
  const groups: NavGroup[] = [];
  for (const group of NAV_GROUPS) {
    const items = group.keys
      .filter((key) => byKey.has(key))
      .map((key) => {
        grouped.add(key);
        return byKey.get(key) as NavEntry;
      });
    if (items.length > 0) {
      groups.push({ key: group.keys.join('-'), label: group.label, entries: items });
    }
  }
  const fallback = entries.filter((entry) => !grouped.has(entry.key));
  if (fallback.length > 0) {
    groups.push({ key: 'other', label: '其他', entries: fallback });
  }
  return groups;
}
