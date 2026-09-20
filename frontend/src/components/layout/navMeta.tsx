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
