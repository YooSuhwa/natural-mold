'use client'

import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/shared/empty-state'
import { ErrorState } from '@/components/shared/error-state'

interface ResourceListStateProps {
  loading?: boolean
  error?: boolean
  isFiltered?: boolean
  skeleton: ReactNode
  emptyTitle: string
  emptyDescription?: string
  filteredEmptyTitle: string
  filteredEmptyDescription?: string
  errorTitle?: string
  errorDescription?: string
  retryLabel?: string
  onRetry?: () => void
}

export function ResourceListState({
  loading,
  error,
  isFiltered,
  skeleton,
  emptyTitle,
  emptyDescription,
  filteredEmptyTitle,
  filteredEmptyDescription,
  errorTitle,
  errorDescription,
  retryLabel,
  onRetry,
}: ResourceListStateProps) {
  if (loading) return <>{skeleton}</>

  if (error) {
    return <ErrorState title={errorTitle} description={errorDescription} onRetry={onRetry} />
  }

  if (isFiltered) {
    return (
      <EmptyState
        title={filteredEmptyTitle}
        description={filteredEmptyDescription}
        action={
          onRetry && retryLabel ? (
            <Button type="button" variant="outline" onClick={onRetry}>
              {retryLabel}
            </Button>
          ) : null
        }
      />
    )
  }

  return <EmptyState title={emptyTitle} description={emptyDescription} />
}
