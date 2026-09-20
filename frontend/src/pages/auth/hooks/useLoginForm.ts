/**
 * 登录表单状态与提交。
 */

import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { errorMessage } from '@/components/common/StateViews';
import { useLoginMutation } from '@/lib/queries';

export interface LoginFormState {
  username: string;
  password: string;
  error: string;
  submitting: boolean;
  setUsername: (value: string) => void;
  setPassword: (value: string) => void;
  submit: (event: FormEvent) => void;
}

export function useLoginForm(): LoginFormState {
  const navigate = useNavigate();
  const login = useLoginMutation();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setError('');
    if (!username.trim() || !password) {
      setError('请输入用户名与密码');
      return;
    }
    login.mutate(
      { username: username.trim(), password },
      {
        onSuccess: () => navigate('/', { replace: true }),
        onError: (mutationError) => setError(errorMessage(mutationError)),
      },
    );
  };

  return {
    username,
    password,
    error,
    submitting: login.isPending,
    setUsername,
    setPassword,
    submit,
  };
}
