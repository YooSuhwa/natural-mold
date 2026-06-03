import type { ReactNode } from 'react'
import { EmptyStateIcon, type DomainIconId } from '@/components/shared/icon'
import { cn } from '@/lib/utils'

interface EmptyStateProps {
  icon?: ReactNode
  iconId?: string | null
  iconFallback?: DomainIconId
  title: string
  description?: string
  action?: ReactNode
  className?: string
}

export function EmptyState({
  icon,
  iconId,
  iconFallback,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-4 rounded-2xl border border-dashed border-primary-strong/25 bg-[var(--moldy-surface)] p-12 text-center shadow-[var(--moldy-shadow-card)]',
        className,
      )}
    >
      {icon ? (
        <div className="flex size-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground ring-1 ring-primary-strong/15">
          {icon}
        </div>
      ) : iconId ? (
        <EmptyStateIcon iconId={iconId} fallback={iconFallback} />
      ) : null}
      <div className="space-y-1">
        <p className="text-sm font-semibold text-foreground">{title}</p>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  )
}
