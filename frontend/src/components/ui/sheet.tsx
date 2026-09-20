import * as React from 'react';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface SheetProps {
  /** 是否打开 */
  open: boolean;
  /** 关闭回调（遮罩点击 / Esc / 关闭按钮） */
  onOpenChange: (open: boolean) => void;
  /** 抽屉标题，用于 aria-labelledby */
  title?: React.ReactNode;
  /** 标题下方说明 */
  description?: React.ReactNode;
  /** 停靠边，默认右侧（Agent 抽屉场景） */
  side?: 'right' | 'left';
  /** 是否显示右上角关闭按钮 */
  showClose?: boolean;
  /** 是否锁定 body 滚动（默认 true；Agent 抽屉等非阻塞场景传 false） */
  lockScroll?: boolean;
  className?: string;
  children?: React.ReactNode;
}

/**
 * 轻量无障碍抽屉（不依赖 radix）。
 *
 * - 打开时锁定 body 滚动，Esc 关闭，焦点移入面板
 * - `role="dialog"` + `aria-modal`，遮罩点击关闭
 */
export function Sheet({
  open,
  onOpenChange,
  title,
  description,
  side = 'right',
  showClose = true,
  lockScroll = true,
  className,
  children,
}: SheetProps) {
  const panelRef = React.useRef<HTMLDivElement>(null);
  const titleId = React.useId();

  React.useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    if (lockScroll) document.body.style.overflow = 'hidden';
    panelRef.current?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onOpenChange(false);
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      if (lockScroll) document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open, onOpenChange, lockScroll]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50" role="presentation">
      <div
        className="bg-background/80 absolute inset-0 backdrop-blur-sm"
        onClick={() => onOpenChange(false)}
        aria-hidden="true"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        tabIndex={-1}
        className={cn(
          'bg-background fixed inset-y-0 flex w-full max-w-md flex-col gap-4 border p-6 shadow-lg outline-none sm:max-w-lg',
          side === 'right' ? 'right-0 border-l' : 'left-0 border-r',
          className,
        )}
      >
        {(title || showClose) && (
          <div className="flex items-start justify-between gap-4">
            <div className="space-y-1">
              {title && (
                <h2 id={titleId} className="text-lg font-semibold leading-none tracking-tight">
                  {title}
                </h2>
              )}
              {description && (
                <p className="text-muted-foreground text-sm">{description}</p>
              )}
            </div>
            {showClose && (
              <button
                type="button"
                aria-label="关闭"
                onClick={() => onOpenChange(false)}
                className="text-muted-foreground hover:text-foreground rounded-md p-1 transition-colors"
              >
                <X className="size-4" />
              </button>
            )}
          </div>
        )}
        <div className="flex-1 overflow-y-auto">{children}</div>
      </div>
    </div>
  );
}