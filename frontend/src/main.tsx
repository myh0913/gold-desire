import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import App from './App';
import { queryClient } from './lib/queryClient';
import './index.css';

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('未找到 #root 挂载节点');
}

// Vite 的 base (如 '/gd/') 不会自动作用于 React Router，
// 必须显式传给 BrowserRouter.basename，否则 <Link to="/x"> 会生成 /x 而不是 /gd/x。
// 去掉尾部斜杠：React Router v6 的 basename 约定不带尾斜杠。
const basename = import.meta.env.BASE_URL.replace(/\/+$/, '') || '/';

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={basename}>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);