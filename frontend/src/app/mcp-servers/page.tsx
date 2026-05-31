'use client'

import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import type { ColumnDef } from '@tanstack/react-table'
import { Activity, Download, Plus, Upload } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { announceHealthResult } from '@/lib/health-check-toast'

import { Button } from '@/components/ui/button'
import { PageHeader } from '@/components/shared/page-header'
import { DataTable } from '@/components/ui/data-table'
import { StatusChip } from '@/components/shared/status-chip'
import { EmptyState } from '@/components/shared/empty-state'
import { DomainIconTile, getDomainIconIdForMcpTransport } from '@/components/shared/icon'
import { McpServerWizard } from '@/components/mcp/mcp-server-wizard'
import { McpServerDetailDialog } from '@/components/mcp/mcp-server-detail-dialog'
import { McpImportDialog } from '@/components/mcp/mcp-import-dialog'
import { useExportMcpServers, useMcpServers } from '@/lib/hooks/use-mcp-servers'
import { useMcpHealth, useRunHealthCheck } from '@/lib/hooks/use-health'
import type { McpServer } from '@/lib/types/mcp'
import type { HealthCheckEntry } from '@/lib/types/health'

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

export default function McpServersPage() {
  const t = useTranslations('mcp.page')
  const { data: servers, isLoading } = useMcpServers()
  const { data: healthEntries } = useMcpHealth()
  const runHealthCheck = useRunHealthCheck()
  const [wizardOpen, setWizardOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [detailId, setDetailId] = useState<string | null>(null)
  const exportMutation = useExportMcpServers()

  async function handleExport() {
    try {
      const data = await exportMutation.mutateAsync()
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: 'application/json',
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `mcp-servers-${new Date().toISOString().slice(0, 10)}.json`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      const count = Object.keys(data.mcpServers).length
      toast.success(t('toast.exported', { count }))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.exportFailed'))
    }
  }

  // Latest health probe per server. Falls back to the server's own
  // `status` field when no probe row exists yet.
  const healthByServer = useMemo(() => {
    const map = new Map<string, HealthCheckEntry>()
    ;(healthEntries ?? []).forEach((h) => map.set(h.target_id, h))
    return map
  }, [healthEntries])

  async function handleCheckNow(serverId: string) {
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

  const columns = useMemo<ColumnDef<McpServer>[]>(
    () => [
      {
        accessorKey: 'name',
        header: t('columns.name'),
        cell: ({ row }) => (
          <span className="inline-flex items-center gap-2 font-medium">
            <DomainIconTile
              iconId={getDomainIconIdForMcpTransport(row.original.transport)}
              className="size-8"
              iconClassName="size-4"
            />
            {row.original.name}
          </span>
        ),
      },
      {
        accessorKey: 'transport',
        header: t('columns.transport'),
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground">{row.original.transport}</span>
        ),
      },
      {
        accessorKey: 'last_tool_count',
        header: t('columns.tools'),
        cell: ({ row }) => row.original.last_tool_count ?? 0,
      },
      {
        id: 'status',
        header: t('columns.status'),
        cell: ({ row }) => {
          const entry = healthByServer.get(row.original.id)
          // Health probe wins if available; otherwise the static MCP status.
          if (entry) {
            return (
              <div className="flex flex-col items-start gap-0.5">
                <StatusChip variant={entry.status} />
                <span className="text-[10px] text-muted-foreground">
                  {formatRelativeTime(entry.checked_at, t)}
                </span>
              </div>
            )
          }
          return <StatusChip variant={row.original.status} />
        },
      },
      {
        accessorKey: 'last_pinged_at',
        header: t('columns.lastResponse'),
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground">
            {formatDate(row.original.last_pinged_at)}
          </span>
        ),
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => (
          <Button
            variant="ghost"
            size="sm"
            aria-label={t('actions.checkNowFor', { name: row.original.name })}
            data-testid={`check-now-${row.original.id}`}
            onClick={(e) => {
              e.stopPropagation()
              handleCheckNow(row.original.id)
            }}
            disabled={runHealthCheck.isPending}
          >
            <Activity className="size-3.5" />
            {t('actions.checkNow')}
          </Button>
        ),
        enableSorting: false,
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [healthByServer, runHealthCheck.isPending, t],
  )

  const data = servers ?? []

  return (
    <div className="flex flex-1 flex-col overflow-auto bg-gradient-to-b from-emerald-50/40 via-background to-background dark:from-emerald-950/15 dark:via-background dark:to-background">
      <div className="mx-auto flex w-full max-w-[1180px] flex-1 flex-col gap-6 px-6 py-7 pb-20 md:px-8">
        <PageHeader
          title={t('title')}
          description={t('description')}
          action={
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="outline" onClick={() => setImportOpen(true)}>
                <Download className="size-4" />
                {t('actions.import')}
              </Button>
              <Button variant="outline" onClick={handleExport} disabled={exportMutation.isPending}>
                <Upload className="size-4" />
                {t('actions.export')}
              </Button>
              <Button onClick={() => setWizardOpen(true)}>
                <Plus className="size-4" />
                {t('actions.new')}
              </Button>
            </div>
          }
        />

        {!isLoading && data.length === 0 ? (
          <EmptyState
            iconId="mcp"
            title={t('empty.title')}
            description={t('empty.description')}
            action={
              <Button onClick={() => setWizardOpen(true)}>
                <Plus className="size-4" />
                {t('empty.action')}
              </Button>
            }
          />
        ) : (
          <DataTable
            columns={columns}
            data={data}
            loading={isLoading}
            searchable
            searchPlaceholder={t('searchPlaceholder')}
            onRowClick={(row) => setDetailId(row.id)}
            emptyTitle={t('empty.filtered')}
          />
        )}

        <McpServerWizard open={wizardOpen} onOpenChange={setWizardOpen} />
        <McpImportDialog open={importOpen} onOpenChange={setImportOpen} />
        <McpServerDetailDialog
          serverId={detailId}
          open={!!detailId}
          onOpenChange={(open) => !open && setDetailId(null)}
        />
      </div>
    </div>
  )
}

function formatRelativeTime(iso: string, t: ReturnType<typeof useTranslations>): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const deltaSec = Math.floor((Date.now() - then) / 1000)
  if (deltaSec < 60) return t('relative.secondsAgo', { count: deltaSec })
  if (deltaSec < 3600) return t('relative.minutesAgo', { count: Math.floor(deltaSec / 60) })
  if (deltaSec < 86400) return t('relative.hoursAgo', { count: Math.floor(deltaSec / 3600) })
  return t('relative.daysAgo', { count: Math.floor(deltaSec / 86400) })
}
