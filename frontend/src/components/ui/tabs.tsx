import * as React from 'react';
import { cn } from '@/lib/utils';

interface TabsContextValue {
  value: string;
  setValue: (next: string) => void;
  baseId: string;
}

const TabsContext = React.createContext<TabsContextValue | null>(null);

function useTabsContext(component: string): TabsContextValue {
  const ctx = React.useContext(TabsContext);
  if (!ctx) {
    throw new Error(`${component} 必须置于 <Tabs> 内使用`);
  }
  return ctx;
}

export interface TabsProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 受控值 */
  value?: string;
  /** 非受控初始值 */
  defaultValue?: string;
  /** 值变化回调 */
  onValueChange?: (value: string) => void;
}

/** 无障碍 Tabs 容器（受控 / 非受控均支持，键盘左右方向键可切换）。 */
function Tabs({
  value,
  defaultValue,
  onValueChange,
  className,
  children,
  ...props
}: TabsProps) {
  const baseId = React.useId();
  const [internal, setInternal] = React.useState(defaultValue ?? '');
  const current = value ?? internal;

  const setValue = React.useCallback(
    (next: string) => {
      if (value === undefined) setInternal(next);
      onValueChange?.(next);
    },
    [onValueChange, value],
  );

  const ctx = React.useMemo<TabsContextValue>(
    () => ({ value: current, setValue, baseId }),
    [current, setValue, baseId],
  );

  return (
    <TabsContext.Provider value={ctx}>
      <div className={cn('flex flex-col gap-3', className)} {...props}>
        {children}
      </div>
    </TabsContext.Provider>
  );
}

/** 标签栏容器。 */
function TabsList({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const { baseId } = useTabsContext('TabsList');
  return (
    <div
      role="tablist"
      id={`${baseId}-tablist`}
      className={cn(
        'bg-muted text-muted-foreground inline-flex h-9 items-center justify-center rounded-lg p-1',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export interface TabsTriggerProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** 该触发项对应的值 */
  value: string;
}

/** 单个标签触发项。 */
function TabsTrigger({ value, className, children, ...props }: TabsTriggerProps) {
  const ctx = useTabsContext('TabsTrigger');
  const selected = ctx.value === value;
  return (
    <button
      type="button"
      role="tab"
      id={`${ctx.baseId}-tab-${value}`}
      aria-selected={selected}
      aria-controls={`${ctx.baseId}-panel-${value}`}
      tabIndex={selected ? 0 : -1}
      onClick={() => ctx.setValue(value)}
      onKeyDown={(event) => {
        if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
          event.preventDefault();
          const tablist = event.currentTarget.parentElement;
          if (!tablist) return;
          const tabs = Array.from(
            tablist.querySelectorAll<HTMLButtonElement>('[role="tab"]'),
          );
          const index = tabs.indexOf(event.currentTarget);
          const next =
            event.key === 'ArrowRight'
              ? tabs[(index + 1) % tabs.length]
              : tabs[(index - 1 + tabs.length) % tabs.length];
          next?.focus();
          const nextValue = next?.dataset.value;
          if (nextValue) ctx.setValue(nextValue);
        }
      }}
      data-value={value}
      className={cn(
        'inline-flex items-center justify-center whitespace-nowrap rounded-md px-3 py-1 text-sm font-medium transition-all focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50',
        selected
          ? 'bg-background text-foreground shadow'
          : 'hover:text-foreground/80',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

export interface TabsContentProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 对应 :class:`TabsTrigger` 的值 */
  value: string;
}

/** 标签内容面板。 */
function TabsContent({ value, className, children, ...props }: TabsContentProps) {
  const ctx = useTabsContext('TabsContent');
  if (ctx.value !== value) return null;
  return (
    <div
      role="tabpanel"
      id={`${ctx.baseId}-panel-${value}`}
      aria-labelledby={`${ctx.baseId}-tab-${value}`}
      className={cn('focus-visible:outline-none', className)}
      {...props}
    >
      {children}
    </div>
  );
}

export { Tabs, TabsList, TabsTrigger, TabsContent };