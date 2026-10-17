'use client'

import { useMemo } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Activity, Loader2 } from 'lucide-react'

import { announceHealthResult } from '@/lib/health-check-toast'
import { Button } from '@/components/ui/button'
import { StatusChip } from '@/components/shared/status-chip'
import { HealthHistoryChart } from '@/components/shared/health-history-chart'
import { useApiErrorMessage } from '@/components/shared/api-error-state'
import { useMcpHealth, useRunHealthCheck } from '@/lib/hooks/use-health'
import { formatDisplayDateTime } from '@/lib/utils/display-format'

/** Health status, history and (with the server's `can_edit`) a manual check. */
export function McpHealthSection({ serverId, canCheck }: { serverId: string; canCheck: boolean }) {
  const t = useTranslations('mcp.detail')
  const { data: healthEntries } = useMcpHealth()
  const runHealthCheck = useRunHealthCheck()
  const errorMessage = useApiErrorMessage()
  const latest = useMemo(
    () => (healthEntries ?? []).find((h) => h.target_id === serverId),
    [healthEntries, serverId],
  )

  async function handleCheck() {
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

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t('health')}
        </h3>
        {canCheck ? (
          <Button
            size="sm"
            variant="outline"
            onClick={handleCheck}
            disabled={runHealthCheck.isPending}
            data-testid="mcp-health-check-now"
          >
            {runHealthCheck.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Activity className="size-3.5" />
            )}
            {t('checkNow')}
          </Button>
        ) : null}
      </div>
      {latest ? (
        <div className="flex items-center gap-2 rounded-md border border-border/60 bg-muted/30 p-2 text-xs">
          <StatusChip variant={latest.status} />
          {typeof latest.latency_ms === 'number' ? (
            <span className="text-muted-foreground">{latest.latency_ms} ms</span>
          ) : null}
          <span className="moldy-ui-micro text-muted-foreground">
            {formatDisplayDateTime(latest.checked_at)}
          </span>
        </div>
      ) : null}
      <HealthHistoryChart targetKind="mcp_server" targetId={serverId} />
    </div>
  )
}
