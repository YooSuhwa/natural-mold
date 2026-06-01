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
        'flex flex-col items-center justify-center gap-4 rounded-xl border border-dashed p-12 text-center',
        className,
      )}
    >
      {icon ? (
        <div className="flex size-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
          {icon}
        </div>
      ) : iconId ? (
        <EmptyStateIcon iconId={iconId} fallback={iconFallback} />
      ) : null}
      <div className="space-y-1">
        <p className="text-sm font-medium text-foreground">{title}</p>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  )
}
