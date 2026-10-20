'use client'

import { PermissionsLoadError } from '@/components/shared/permissions-load-error'
import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Activity, RefreshCw, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusChip } from '@/components/shared/status-chip'
import { ApiErrorState, useApiErrorMessage } from '@/components/shared/api-error-state'
import { DialogShell } from '@/components/shared/dialog-shell'
import { DomainIconTile, getDomainIconIdForMcpTransport } from '@/components/shared/icon'
import { DeleteConfirmInline } from '@/components/shared/delete-confirm-inline'
import { CredentialPicker } from '@/components/credential/credential-picker'
import { CredentialModeControl } from '@/components/readiness/credential-mode-control'
import { useCredentialTypes } from '@/lib/hooks/use-credentials'
import { McpHealthSection } from './mcp-health-section'
import { McpToolTable } from './mcp-tool-table'
import {
  useDeleteMcpServer,
  useDiscoverMcpTools,
  useMcpServer,
  useTestMcpServer,
  useUpdateMcpServer,
} from '@/lib/hooks/use-mcp-servers'
import { useResourceAccess } from '@/lib/authz/use-resource-access'

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
  const { data: server, isLoading, error } = useMcpServer(serverId)
  const update = useUpdateMcpServer()
  const remove = useDeleteMcpServer()
  const test = useTestMcpServer()
  const discover = useDiscoverMcpTools()
  const [confirming, setConfirming] = useState(false)
  const errorMessage = useApiErrorMessage()
  const rows = useMemo(() => (server ? [server] : []), [server])
  const access = useResourceAccess('mcp_server', rows)
  // Plan 13.9: test, tool refresh, credential and health check need can_edit (hidden
  // from runners), deletion can_delete.
  const canEdit = access.can(server, 'can_edit')
  const { data: credentialTypes } = useCredentialTypes()
  const credentialTypeKeys = useMemo(
    () => (credentialTypes ?? []).map((type) => type.key),
    [credentialTypes],
  )
  const canDelete = access.can(server, 'can_delete')

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
      toast.error(errorMessage(e, e instanceof Error ? e.message : t('toast.testFailed')))
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
      toast.error(errorMessage(e, e instanceof Error ? e.message : t('toast.discoveryFailed')))
    }
  }

  async function handleDelete() {
    if (!server) return
    try {
      await remove.mutateAsync(server.id)
      toast.success(t('toast.deleted'))
      onOpenChange(false)
    } catch (e) {
      toast.error(errorMessage(e, e instanceof Error ? e.message : t('toast.deleteFailed')))
    }
  }

  async function handleCredentialChange(next: string | null) {
    if (!server) return
    try {
      await update.mutateAsync({ id: server.id, data: { credential_id: next } })
      toast.success(t('toast.credentialUpdated'))
    } catch (e) {
      toast.error(errorMessage(e, e instanceof Error ? e.message : t('toast.updateFailed')))
    }
  }

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="lg" height="tall">
      {error && serverId ? (
        <>
          <DialogShell.Header title={t('loading')} />
          <DialogShell.Body>
            <ApiErrorState error={error} resource={{ type: 'mcp_server', id: serverId }} />
          </DialogShell.Body>
        </>
      ) : isLoading || !server ? (
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
                <StatusChip variant={server.health_status ?? server.status} />
              </div>
            }
          />
          <DialogShell.Body>
            <PermissionsLoadError error={access.error} onRetry={() => void access.refetch()} />
            {canEdit ? (
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
            ) : null}

            <div className="space-y-1.5">
              <label className="text-xs font-medium">{t('endpoint')}</label>
              <div className="rounded-md border border-border/60 bg-muted/40 p-2 font-mono text-xs break-all">
                {server.transport === 'stdio' ? server.command : server.url}
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium">{t('credential')}</label>
              <CredentialPicker
                includeCompany
                disabled={!canEdit}
                value={server.credential_id}
                onChange={handleCredentialChange}
              />
            </div>

            {canEdit ? (
              <CredentialModeControl
                resourceType="mcp_server"
                resourceId={server.id}
                mode={server.credential_mode ?? (server.credential_id ? 'shared' : 'none')}
                credentialId={server.credential_id}
                definitionKey={server.credential_definition_key ?? null}
                acceptedKeys={credentialTypeKeys}
              />
            ) : null}

            <McpHealthSection serverId={server.id} canCheck={canEdit} />

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
            ) : canDelete ? (
              <Button
                variant="ghost"
                size="sm"
                className="mr-auto text-destructive hover:bg-destructive/10 hover:text-destructive"
                onClick={() => setConfirming(true)}
              >
                <Trash2 className="size-3.5" />
                {t('delete')}
              </Button>
            ) : (
              <span className="mr-auto" />
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
