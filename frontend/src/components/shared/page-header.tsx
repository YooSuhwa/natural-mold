import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface PageHeaderProps {
  title: string
  description?: string
  action?: ReactNode
  className?: string
}

export function PageHeader({ title, description, action, className }: PageHeaderProps) {
  return (
    <div className={cn('flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between', className)}>
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex items-center gap-2">
          <span aria-hidden className="moldy-page-kicker" />
          <h1 className="moldy-page-title leading-tight">{title}</h1>
        </div>
        {description && (
          <p className="max-w-3xl text-sm leading-6 text-pretty text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {action ? <div className="flex shrink-0 flex-wrap items-center gap-2">{action}</div> : null}
    </div>
  )
}
