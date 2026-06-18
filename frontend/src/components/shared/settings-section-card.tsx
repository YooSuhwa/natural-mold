import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface SettingsSectionCardProps {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}

export function SettingsSectionCard({
  title,
  description,
  actions,
  children,
  className,
}: SettingsSectionCardProps) {
  return (
    <section className={cn('moldy-card p-5', className)}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1">
          <h2 className="moldy-ui-subtitle text-foreground">{title}</h2>
          {description ? (
            <p className="moldy-ui-copy text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
      <div className="mt-5">{children}</div>
    </section>
  )
}
