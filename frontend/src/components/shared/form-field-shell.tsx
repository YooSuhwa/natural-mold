import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface FormFieldShellProps {
  id?: string
  label: ReactNode
  description?: ReactNode
  error?: ReactNode
  actions?: ReactNode
  required?: boolean
  children: ReactNode
  className?: string
  layout?: 'stacked' | 'inline'
}

export function FormFieldShell({
  id,
  label,
  description,
  error,
  actions,
  required,
  children,
  className,
  layout = 'stacked',
}: FormFieldShellProps) {
  const descriptionId = id ? `${id}-description` : undefined
  const errorId = id ? `${id}-error` : undefined
  const labelNode = (
    <label htmlFor={id} className="moldy-ui-label text-foreground">
      {label}
      {required ? <span className="text-status-danger"> *</span> : null}
    </label>
  )

  if (layout === 'inline') {
    return (
      <div className={cn('flex gap-3', className)}>
        <div className="pt-0.5">{children}</div>
        <div className="min-w-0 space-y-1">
          {labelNode}
          {description ? (
            <p id={descriptionId} className="moldy-ui-caption text-muted-foreground">
              {description}
            </p>
          ) : null}
          {error ? (
            <p id={errorId} className="moldy-ui-caption text-status-danger">
              {error}
            </p>
          ) : null}
        </div>
      </div>
    )
  }

  return (
    <div className={cn('space-y-1.5', className)}>
      {actions ? (
        <div className="flex items-center justify-between gap-2">
          {labelNode}
          <div className="flex shrink-0 items-center gap-2">{actions}</div>
        </div>
      ) : (
        labelNode
      )}
      {description ? (
        <p id={descriptionId} className="moldy-ui-caption text-muted-foreground">
          {description}
        </p>
      ) : null}
      {children}
      {error ? (
        <p id={errorId} className="moldy-ui-caption text-status-danger">
          {error}
        </p>
      ) : null}
    </div>
  )
}
