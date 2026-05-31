'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Activity, Loader2, RefreshCw, Trash2 } from 'lucide-react'

import { announceHealthResult } from '@/lib/health-check-toast'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusChip } from '@/components/shared/status-chip'
import { HealthHistoryChart } from '@/components/shared/health-history-chart'
import { DialogShell } from '@/components/shared/dialog-shell'
import { DomainIconTile, getDomainIconIdForMcpTransport } from '@/components/shared/icon'
import { DeleteConfirmInline } from '@/components/shared/delete-confirm-inline'
import { CredentialPicker } from '@/components/credential/credential-picker'
import { McpToolTable } from './mcp-tool-table'
import {
  useDeleteMcpServer,
  useDiscoverMcpTools,
  useMcpServer,
  useTestMcpServer,
  useUpdateMcpServer,
} from '@/lib/hooks/use-mcp-servers'
import { useMcpHealth, useRunHealthCheck } from '@/lib/hooks/use-health'

interface Props {
  serverId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function McpServerDetailDialog(props: Props) {
  return <McpServerDetailDialogInner key={props.serverId ?? 'closed'} {...props} />
}

function McpServerDetailDialogInner({ serverId, open, onOpenChange }: Props) {
  const t = useTranslations('mcp.detail')
  const tc = useTranslations('common')
  const { data: server, isLoading } = useMcpServer(serverId)
  const update = useUpdateMcpServer()
  const remove = useDeleteMcpServer()
  const test = useTestMcpServer()
  const discover = useDiscoverMcpTools()
  const [confirming, setConfirming] = useState(false)

  async function handleTest() {
    if (!server) return
    try {
      const result = await test.mutateAsync(server.id)
      if (result.success) {
        toast.success(t('toast.connected', { count: result.tool_count }))
      } else {
        toast.error(result.error ?? t('toast.connectionFailed'))
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.testFailed'))
    }
  }

  async function handleDiscover() {
    if (!server) return
    try {
      const result = await discover.mutateAsync(server.id)
      if (result.success) {
        toast.success(t('toast.importedTools', { count: result.tools.length }))
      } else {
        toast.error(result.error ?? t('toast.discoveryFailed'))
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.discoveryFailed'))
    }
  }

  async function handleDelete() {
    if (!server) return
    try {
      await remove.mutateAsync(server.id)
      toast.success(t('toast.deleted'))
      onOpenChange(false)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.deleteFailed'))
    }
  }

  async function handleCredentialChange(next: string | null) {
    if (!server) return
    try {
      await update.mutateAsync({ id: server.id, data: { credential_id: next } })
      toast.success(t('toast.credentialUpdated'))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.updateFailed'))
    }
  }

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="lg" height="tall">
      {isLoading || !server ? (
        <>
          <DialogShell.Header title={t('loading')} />
          <DialogShell.Body>
            <Skeleton className="h-40 w-full rounded-lg" />
          </DialogShell.Body>
          <DialogShell.Footer>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {tc('close')}
            </Button>
          </DialogShell.Footer>
        </>
      ) : (
        <>
          <DialogShell.Header
            icon={
              <DomainIconTile
                iconId={getDomainIconIdForMcpTransport(server.transport)}
                className="size-9"
                iconClassName="size-5"
              />
            }
            title={server.name}
            description={server.transport}
            actions={
              <div className="flex items-center gap-2">
                {server.is_system ? (
                  <span className="inline-flex items-center rounded-full bg-status-info/15 px-2 py-0.5 text-xs font-medium text-status-info">
                    {t('system')}
                  </span>
                ) : null}
                <StatusChip
                  variant={server.health_status ?? server.status}
                />
              </div>
            }
          />
          <DialogShell.Body>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={handleTest} disabled={test.isPending}>
                <Activity className="size-3.5" /> {t('test')}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={handleDiscover}
                disabled={discover.isPending}
              >
                <RefreshCw className="size-3.5" /> {t('refreshTools')}
              </Button>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium">{t('endpoint')}</label>
              <div className="rounded-md border border-border/60 bg-muted/40 p-2 font-mono text-xs break-all">
                {server.transport === 'stdio' ? server.command : server.url}
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium">{t('credential')}</label>
              <CredentialPicker
                value={server.credential_id}
                onChange={handleCredentialChange}
              />
            </div>

            <McpHealthSection serverId={server.id} />

            <div className="space-y-1.5">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t('importedTools', { count: server.tools.length })}
              </h3>
              <McpToolTable tools={server.tools} />
            </div>
          </DialogShell.Body>
          <DialogShell.Footer>
            {confirming ? (
              <div className="flex-1">
                <DeleteConfirmInline
                  entity={t('serverEntity')}
                  onCancel={() => setConfirming(false)}
                  onConfirm={handleDelete}
                  pending={remove.isPending}
                />
              </div>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                className="mr-auto text-destructive hover:bg-destructive/10 hover:text-destructive"
                onClick={() => setConfirming(true)}
              >
                <Trash2 className="size-3.5" />
                {t('delete')}
              </Button>
            )}
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {tc('close')}
            </Button>
          </DialogShell.Footer>
        </>
      )}
    </DialogShell>
  )
}

function McpHealthSection({ serverId }: { serverId: string }) {
  const t = useTranslations('mcp.detail')
  const { data: healthEntries } = useMcpHealth()
  const runHealthCheck = useRunHealthCheck()
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
      toast.error(e instanceof Error ? e.message : t('toast.healthCheckFailed'))
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t('health')}
        </h3>
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
      </div>
      {latest ? (
        <div className="flex items-center gap-2 rounded-md border border-border/60 bg-muted/30 p-2 text-xs">
          <StatusChip variant={latest.status} />
          {typeof latest.latency_ms === 'number' ? (
            <span className="text-muted-foreground">{latest.latency_ms} ms</span>
          ) : null}
          <span className="text-[10px] text-muted-foreground">
            {new Date(latest.checked_at).toLocaleString()}
          </span>
        </div>
      ) : null}
      <HealthHistoryChart targetKind="mcp_server" targetId={serverId} />
    </div>
  )
}
