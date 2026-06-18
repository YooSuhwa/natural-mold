'use client'

import { ErrorState } from '@/components/shared/error-state'

type RouteErrorProps = {
  readonly error: Error
  readonly reset: () => void
}

export default function Error({ reset }: RouteErrorProps) {
  return (
    <div className="moldy-app-surface flex min-h-0 flex-1 items-center justify-center p-6">
      <ErrorState onRetry={reset} />
    </div>
  )
}
