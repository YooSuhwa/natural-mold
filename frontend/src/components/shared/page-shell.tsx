'use client'

import type { ReactNode } from 'react'

import { PageHeader } from '@/components/shared/page-header'
import { ErrorState } from '@/components/shared/error-state'

interface Props {
  title: string
  description?: string
  action?: ReactNode
  isError?: boolean
  errorTitle?: string
  errorDescription?: string
  onRetry?: () => void
  children: ReactNode
}

export function PageShell({
  title,
  description,
  action,
  isError,
  errorTitle,
  errorDescription,
  onRetry,
  children,
}: Props) {
  return (
    <div className="moldy-app-surface flex flex-1 flex-col overflow-auto">
      <div className="mx-auto flex w-full max-w-[1180px] flex-1 flex-col gap-6 px-6 py-7 pb-20 md:px-8">
        <PageHeader title={title} description={description} action={action} />
        {isError ? (
          <ErrorState title={errorTitle} description={errorDescription} onRetry={onRetry} />
        ) : (
          children
        )}
      </div>
    </div>
  )
}
