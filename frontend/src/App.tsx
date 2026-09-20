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
import PlaceholderPage from '@/pages/PlaceholderPage';

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
        {/* Phase 2 占位：路由必须挂上，否则侧栏点了会命中通配显示无权限。 */}
        <Route path="/advice" element={<RequirePage page="advice"><PlaceholderPage title="今日建议" /></RequirePage>} />
        <Route path="/review" element={<RequirePage page="review"><PlaceholderPage title="复盘" /></RequirePage>} />
        <Route path="/pools" element={<RequirePage page="pools"><PlaceholderPage title="涨停池" /></RequirePage>} />
        <Route path="/ladder" element={<RequirePage page="ladder"><PlaceholderPage title="连板天梯" /></RequirePage>} />
        <Route path="/newsflash" element={<RequirePage page="newsflash"><PlaceholderPage title="快讯" /></RequirePage>} />
        <Route path="/themes" element={<RequirePage page="themes"><PlaceholderPage title="主题" /></RequirePage>} />
        <Route path="/monitor" element={<RequirePage page="monitor"><PlaceholderPage title="监管名单" /></RequirePage>} />
        <Route path="/backtest" element={<RequirePage page="backtest"><PlaceholderPage title="回测" /></RequirePage>} />
        <Route path="*" element={<NoAccessPage />} />
      </Route>
    </Routes>
  );
}
