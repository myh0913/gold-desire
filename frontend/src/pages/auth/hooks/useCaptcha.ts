/**
 * 验证码获取：包装 `useCaptchaQuery`，提供「换一张」。
 */

import { useCaptchaQuery } from '@/lib/queries';

export interface CaptchaState {
  image?: string;
  captchaId?: string;
  loading: boolean;
  error: unknown;
  refresh: () => void;
}

export function useCaptcha(enabled = true): CaptchaState {
  const query = useCaptchaQuery(enabled);
  return {
    image: query.data?.image,
    captchaId: query.data?.captcha_id,
    loading: query.isFetching,
    error: query.error,
    refresh: () => {
      void query.refetch();
    },
  };
}
