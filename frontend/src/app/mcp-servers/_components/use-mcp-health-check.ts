'use client'

import { useMemo } from 'react'
import { toast } from 'sonner'
import { useTranslations } from 'next-intl'

import { useApiErrorMessage } from '@/components/shared/api-error-state'
import { announceHealthResult } from '@/lib/health-check-toast'
import { useMcpHealth, useRunHealthCheck } from '@/lib/hooks/use-health'
import type { HealthCheckEntry } from '@/lib/types/health'

/** Latest health probe per server and the "check now" action. */
export function useMcpHealthCheck() {
  const t = useTranslations('mcp.page')
  const errorMessage = useApiErrorMessage()
  const { data: healthEntries } = useMcpHealth()
  const runHealthCheck = useRunHealthCheck()

  // Falls back to the server's own `status` field when no probe row exists yet.
  const healthByServer = useMemo(() => {
    const map = new Map<string, HealthCheckEntry>()
    ;(healthEntries ?? []).forEach((h) => map.set(h.target_id, h))
    return map
  }, [healthEntries])

  async function checkNow(serverId: string) {
    try {
      const result = await runHealthCheck.mutateAsync({
        targetKind: 'mcp_server',
        targetId: serverId,
      })
      announceHealthResult(result)
    } catch (e) {
      toast.error(errorMessage(e, e instanceof Error ? e.message : t('toast.healthCheckFailed')))
    }
  }

  return { healthByServer, checkNow, checking: runHealthCheck.isPending }
}
