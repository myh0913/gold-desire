/**
 * 无访问权限页（403 / 未知路由）。
 *
 * 前端拦截只是体验优化，服务端接口级鉴权才是权威。
 */

import { Link } from 'react-router-dom';
import { ShieldX } from 'lucide-react';
import { buttonVariants } from '@/components/ui/button';

export interface NoAccessPageProps {
  /** 缺失的页面 key（路由守卫传入） */
  page?: string;
}

export default function NoAccessPage({ page }: NoAccessPageProps) {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 p-6 text-center">
      <ShieldX className="text-muted-foreground size-8" aria-hidden="true" />
      <h1 className="text-xl font-semibold">无访问权限</h1>
      <p className="text-muted-foreground max-w-md text-sm">
        {page
          ? `当前角色未被授予「${page}」页面的访问权限。如需访问，请联系管理员在「设置 → 角色权限」中调整。`
          : '该地址不存在，或当前角色无权访问。'}
      </p>
      <Link to="/" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
        返回总览
      </Link>
    </div>
  );
}
