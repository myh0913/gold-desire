/**
 * 注册页：图形验证码必填，邀请码可选（决定角色）。
 */

import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { AuthCard } from './components/AuthCard';
import { CaptchaField } from './components/CaptchaField';
import { FormField } from './components/FormField';
import { useRegisterForm } from './hooks/useRegisterForm';

export default function RegisterPage() {
  const form = useRegisterForm();

  return (
    <AuthCard
      title="注册"
      description="需要图形验证码；填写邀请码可获得对应角色"
      footer={
        <span>
          已有账号？
          <Link to="/login" className="text-primary underline-offset-4 hover:underline">
            返回登录
          </Link>
        </span>
      }
    >
      <form className="space-y-4" onSubmit={form.submit} noValidate>
        <FormField
          label="用户名"
          value={form.username}
          autoComplete="username"
          hint="至少 3 个字符"
          onChange={(event) => form.setUsername(event.target.value)}
        />
        <FormField
          label="密码"
          type="password"
          value={form.password}
          autoComplete="new-password"
          hint="至少 8 个字符"
          onChange={(event) => form.setPassword(event.target.value)}
        />
        <FormField
          label="确认密码"
          type="password"
          value={form.confirm}
          autoComplete="new-password"
          onChange={(event) => form.setConfirm(event.target.value)}
        />
        <CaptchaField
          value={form.captcha}
          onChange={form.setCaptcha}
          image={form.captchaImage}
          loading={form.captchaLoading}
          onRefresh={form.refreshCaptcha}
        />
        <FormField
          label="邀请码（可选）"
          value={form.inviteCode}
          autoComplete="off"
          hint="不填则为默认访客角色"
          onChange={(event) => form.setInviteCode(event.target.value)}
        />

        {form.error && (
          <p role="alert" className="text-destructive text-sm">
            {form.error}
          </p>
        )}

        <Button type="submit" className="w-full" disabled={form.submitting}>
          {form.submitting ? '注册中…' : '注册'}
        </Button>
      </form>
    </AuthCard>
  );
}
