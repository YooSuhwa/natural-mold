'use client'

import { toast } from 'sonner'
import type { HealthCheckEntry } from '@/lib/types/health'

/**
 * Show the user-facing toast for a "Check now" result.
 *
 * The probe endpoint returns 200 even when the underlying provider failed,
 * so the mutation `onSuccess` path covers both healthy and unhealthy
 * outcomes. Matching the toast severity to the resulting status keeps the
 * popup honest about what the chip is about to display.
 */
export function announceHealthResult(result: HealthCheckEntry): void {
  if (result.status === 'healthy') {
    toast.success(`Healthy · ${result.latency_ms ?? '—'} ms`)
    return
  }
  if (result.status === 'degraded') {
    toast.warning(`Degraded · ${result.error_message ?? 'partial response'}`)
    return
  }
  const detail = result.error_message ?? result.error_kind ?? 'no detail'
  const label = result.status === 'unhealthy' ? 'Unhealthy' : 'Probe failed'
  toast.error(`${label} · ${detail}`)
}
