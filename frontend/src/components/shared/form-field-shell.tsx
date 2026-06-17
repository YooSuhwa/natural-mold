import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface FormFieldShellProps {
  id?: string
  label: ReactNode
  description?: ReactNode
  error?: ReactNode
  required?: boolean
  children: ReactNode
  className?: string
}

export function FormFieldShell({
  id,
  label,
  description,
  error,
  required,
  children,
  className,
}: FormFieldShellProps) {
  const descriptionId = id ? `${id}-description` : undefined
  const errorId = id ? `${id}-error` : undefined

  return (
    <div className={cn('space-y-1.5', className)}>
      <label htmlFor={id} className="moldy-ui-label text-foreground">
        {label}
        {required ? <span className="text-status-danger"> *</span> : null}
      </label>
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
