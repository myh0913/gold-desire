/**
 * 注册表单状态与提交：验证码 + 邀请码。
 */

import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { errorMessage } from '@/components/common/StateViews';
import { useRegisterMutation } from '@/lib/queries';
import { useCaptcha } from './useCaptcha';

export interface RegisterFormState {
  username: string;
  password: string;
  confirm: string;
  captcha: string;
  inviteCode: string;
  error: string;
  submitting: boolean;
  captchaImage?: string;
  captchaLoading: boolean;
  refreshCaptcha: () => void;
  setUsername: (value: string) => void;
  setPassword: (value: string) => void;
  setConfirm: (value: string) => void;
  setCaptcha: (value: string) => void;
  setInviteCode: (value: string) => void;
  submit: (event: FormEvent) => void;
}

export function useRegisterForm(): RegisterFormState {
  const navigate = useNavigate();
  const register = useRegisterMutation();
  const captchaState = useCaptcha(true);

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [captcha, setCaptcha] = useState('');
  const [inviteCode, setInviteCode] = useState('');
  const [error, setError] = useState('');

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setError('');

    if (username.trim().length < 3) {
      setError('用户名至少 3 个字符');
      return;
    }
    if (password.length < 8) {
      setError('密码至少 8 个字符');
      return;
    }
    if (password !== confirm) {
      setError('两次输入的密码不一致');
      return;
    }
    if (!captcha.trim() || !captchaState.captchaId) {
      setError('请输入图形验证码');
      return;
    }

    register.mutate(
      {
        username: username.trim(),
        password,
        captcha_id: captchaState.captchaId,
        captcha: captcha.trim(),
        invite_code: inviteCode.trim() || null,
      },
      {
        onSuccess: () => navigate('/login', { replace: true }),
        onError: (mutationError) => {
          setError(errorMessage(mutationError));
          setCaptcha('');
          captchaState.refresh();
        },
      },
    );
  };

  return {
    username,
    password,
    confirm,
    captcha,
    inviteCode,
    error,
    submitting: register.isPending,
    captchaImage: captchaState.image,
    captchaLoading: captchaState.loading,
    refreshCaptcha: captchaState.refresh,
    setUsername,
    setPassword,
    setConfirm,
    setCaptcha,
    setInviteCode,
    submit,
  };
}
