'use client'

import { ApiErrorState } from './api-error-state'

/**
 * Shown when `GET /api/resources/{type}/permissions` failed (e.g. 503
 * `authz_unavailable`): rows stay read-only meanwhile (fail closed, plan 13.9).
 */
export function PermissionsLoadError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null
  return (
    <div className="mb-3">
      <ApiErrorState error={error} onRetry={onRetry} />
    </div>
  )
}
