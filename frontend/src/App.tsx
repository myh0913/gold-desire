/**
 * 应用路由与守卫。
 *
 * 守卫策略：
 * - 未登录 → `/login`（会话未确定时先显示加载态，避免闪跳）；
 * - 已登录但缺少页面 key → 渲染无权限视图（**服务端仍是权威**，前端仅做体验拦截）；
 * - 已登录访问 `/login`、`/register` → 回到总览。
 */

import type { ReactElement } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { LoadingState } from '@/components/common/StateViews';
import AppLayout from '@/components/layout/AppLayout';
import { useAuth, usePageAccess } from '@/hooks/useAuth';
import LoginPage from '@/pages/auth/LoginPage';
import RegisterPage from '@/pages/auth/RegisterPage';
import NoAccessPage from '@/pages/NoAccessPage';
import OverviewPage from '@/pages/overview/OverviewPage';
import QuantConfigPage from '@/pages/quantconfig/QuantConfigPage';
import SettingsPage from '@/pages/settings/SettingsPage';
import AdvicePage from '@/pages/advice/AdvicePage';
import ReviewPage from '@/pages/review/ReviewPage';
import PoolsPage from '@/pages/pools/PoolsPage';
import LadderPage from '@/pages/ladder/LadderPage';
import NewsflashPage from '@/pages/newsflash/NewsflashPage';
import ThemesPage from '@/pages/themes/ThemesPage';
import MonitorPage from '@/pages/monitor/MonitorPage';
import BacktestPage from '@/pages/backtest/BacktestPage';

function RequireAuth({ children }: { children: ReactElement }) {
  const { isAuthenticated, isLoading } = useAuth();
  if (isLoading) return <LoadingState className="min-h-screen" title="正在恢复会话…" />;
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return children;
}

function RequirePage({ page, children }: { page: string; children: ReactElement }) {
  const { isLoading, hasPage } = usePageAccess();
  if (isLoading) return <LoadingState />;
  if (!hasPage(page)) return <NoAccessPage page={page} />;
  return children;
}

function PublicOnly({ children }: { children: ReactElement }) {
  const { isAuthenticated, isLoading } = useAuth();
  if (isLoading) return <LoadingState className="min-h-screen" title="正在恢复会话…" />;
  if (isAuthenticated) return <Navigate to="/" replace />;
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route
        path="/login"
        element={
          <PublicOnly>
            <LoginPage />
          </PublicOnly>
        }
      />
      <Route
        path="/register"
        element={
          <PublicOnly>
            <RegisterPage />
          </PublicOnly>
        }
      />

      <Route
        element={
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        }
      >
        <Route
          path="/"
          element={
            <RequirePage page="overview">
              <OverviewPage />
            </RequirePage>
          }
        />
        <Route
          path="/quantconfig"
          element={
            <RequirePage page="quantconfig">
              <QuantConfigPage />
            </RequirePage>
          }
        />
        <Route
          path="/settings"
          element={
            <RequirePage page="settings">
              <SettingsPage />
            </RequirePage>
          }
        />
        <Route path="/advice" element={<RequirePage page="advice"><AdvicePage /></RequirePage>} />
        <Route path="/review" element={<RequirePage page="review"><ReviewPage /></RequirePage>} />
        <Route path="/pools" element={<RequirePage page="pools"><PoolsPage /></RequirePage>} />
        <Route path="/ladder" element={<RequirePage page="ladder"><LadderPage /></RequirePage>} />
        <Route path="/newsflash" element={<RequirePage page="newsflash"><NewsflashPage /></RequirePage>} />
        <Route path="/themes" element={<RequirePage page="themes"><ThemesPage /></RequirePage>} />
        <Route path="/monitor" element={<RequirePage page="monitor"><MonitorPage /></RequirePage>} />
        <Route path="/backtest" element={<RequirePage page="backtest"><BacktestPage /></RequirePage>} />
        <Route path="*" element={<NoAccessPage />} />
      </Route>
    </Routes>
  );
}
