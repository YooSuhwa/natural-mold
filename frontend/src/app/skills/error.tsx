'use client'

import { ApiErrorState } from '@/components/shared/api-error-state'

type RouteErrorProps = {
  readonly error: Error
  readonly reset: () => void
}

/** Route boundary: permission, organization and readiness states are told apart (13.1). */
export default function Error({ error, reset }: RouteErrorProps) {
  return (
    <div className="moldy-app-surface flex min-h-0 flex-1 items-center justify-center p-6">
      <ApiErrorState error={error} onRetry={reset} />
    </div>
  )
}
