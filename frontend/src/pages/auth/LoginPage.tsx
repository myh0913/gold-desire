/**
 * 登录页。
 */

import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { AuthCard } from './components/AuthCard';
import { FormField } from './components/FormField';
import { useLoginForm } from './hooks/useLoginForm';

export default function LoginPage() {
  const form = useLoginForm();

  return (
    <AuthCard
      title="登录"
      description="gold-desire 量化平台"
      footer={
        <span>
          还没有账号？
          <Link to="/register" className="text-primary underline-offset-4 hover:underline">
            使用邀请码注册
          </Link>
        </span>
      }
    >
      <form className="space-y-4" onSubmit={form.submit} noValidate>
        <FormField
          label="用户名"
          value={form.username}
          autoComplete="username"
          onChange={(event) => form.setUsername(event.target.value)}
        />
        <FormField
          label="密码"
          type="password"
          value={form.password}
          autoComplete="current-password"
          onChange={(event) => form.setPassword(event.target.value)}
        />

        {form.error && (
          <p role="alert" className="text-destructive text-sm">
            {form.error}
          </p>
        )}

        <Button type="submit" className="w-full" disabled={form.submitting}>
          {form.submitting ? '登录中…' : '登录'}
        </Button>
      </form>
    </AuthCard>
  );
}
