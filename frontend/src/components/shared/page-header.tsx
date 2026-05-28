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
    <div className={cn('flex items-start justify-between gap-4', className)}>
      <div className="min-w-0 flex-1 space-y-1.5">
        <h1 className="text-[26px] font-bold leading-tight tracking-[-0.025em] text-foreground">
          {title}
        </h1>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
    </div>
  )
}
