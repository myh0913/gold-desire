/**
 * 图形验证码字段：输入框 + 图片 + 点击换一张（`GET /api/captcha`）。
 */

import { useId } from 'react';
import { RefreshCw } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { errorMessage } from '@/components/common/StateViews';

export interface CaptchaFieldProps {
  value: string;
  onChange: (value: string) => void;
  /** data URL */
  image?: string;
  loading?: boolean;
  error?: unknown;
  onRefresh: () => void;
}

export function CaptchaField({
  value,
  onChange,
  image,
  loading = false,
  error,
  onRefresh,
}: CaptchaFieldProps) {
  const id = useId();

  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>图形验证码</Label>
      <div className="flex items-center gap-2">
        <Input
          id={id}
          value={value}
          autoComplete="off"
          placeholder="请输入图中字符"
          onChange={(event) => onChange(event.target.value)}
        />
        <button
          type="button"
          onClick={onRefresh}
          title="换一张"
          aria-label="换一张验证码"
          className="border-input bg-muted/30 hover:bg-muted flex h-9 w-28 shrink-0 items-center justify-center overflow-hidden rounded-md border"
        >
          {image ? (
            <img src={image} alt="验证码" className="h-full w-full object-contain" />
          ) : (
            <RefreshCw className={loading ? 'size-4 animate-spin' : 'size-4'} aria-hidden="true" />
          )}
        </button>
      </div>
      {error !== undefined && error !== null && (
        <p role="alert" className="text-destructive text-xs">
          {errorMessage(error)}
        </p>
      )}
    </div>
  );
}
