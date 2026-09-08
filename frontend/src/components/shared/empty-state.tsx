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
    <div className={cn('moldy-empty-state', className)}>
      {icon ? (
        <div className="moldy-empty-state-icon">{icon}</div>
      ) : iconId ? (
        <EmptyStateIcon iconId={iconId} fallback={iconFallback} />
      ) : null}
      <div className="min-w-0 space-y-1 break-keep text-pretty">
        <p className="text-sm font-semibold text-foreground">{title}</p>
        {description && <p className="text-sm leading-6 text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  )
}
