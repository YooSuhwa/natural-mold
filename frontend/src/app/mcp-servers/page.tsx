'use client'

import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import type { ColumnDef } from '@tanstack/react-table'
import { Activity, Plus, Server } from 'lucide-react'

import { announceHealthResult } from '@/lib/health-check-toast'

import { Button } from '@/components/ui/button'
import { PageHeader } from '@/components/shared/page-header'
import { DataTable } from '@/components/ui/data-table'
import { StatusChip } from '@/components/shared/status-chip'
import { EmptyState } from '@/components/shared/empty-state'
import { McpServerWizard } from '@/components/mcp/mcp-server-wizard'
import { McpServerDetailDialog } from '@/components/mcp/mcp-server-detail-dialog'
import { useMcpServers } from '@/lib/hooks/use-mcp-servers'
import { useMcpHealth, useRunHealthCheck } from '@/lib/hooks/use-health'
import type { McpServer } from '@/lib/types/mcp'
import type { HealthCheckEntry } from '@/lib/types/health'

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

export default function McpServersPage() {
  const { data: servers, isLoading } = useMcpServers()
  const { data: healthEntries } = useMcpHealth()
  const runHealthCheck = useRunHealthCheck()
  const [wizardOpen, setWizardOpen] = useState(false)
  const [detailId, setDetailId] = useState<string | null>(null)

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
      toast.error(e instanceof Error ? e.message : 'Health check failed')
    }
  }

  const columns = useMemo<ColumnDef<McpServer>[]>(
    () => [
      {
        accessorKey: 'name',
        header: 'Name',
        cell: ({ row }) => <span className="font-medium">{row.original.name}</span>,
      },
      {
        accessorKey: 'transport',
        header: 'Transport',
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground">{row.original.transport}</span>
        ),
      },
      {
        accessorKey: 'last_tool_count',
        header: 'Tools',
        cell: ({ row }) => row.original.last_tool_count ?? 0,
      },
      {
        id: 'status',
        header: 'Status',
        cell: ({ row }) => {
          const entry = healthByServer.get(row.original.id)
          // Health probe wins if available; otherwise the static MCP status.
          if (entry) {
            return (
              <div className="flex flex-col items-start gap-0.5">
                <StatusChip variant={entry.status} />
                <span className="text-[10px] text-muted-foreground">
                  {formatRelativeTime(entry.checked_at)}
                </span>
              </div>
            )
          }
          return <StatusChip variant={row.original.status} />
        },
      },
      {
        accessorKey: 'last_pinged_at',
        header: 'Last ping',
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
            aria-label={`Check ${row.original.name}`}
            data-testid={`check-now-${row.original.id}`}
            onClick={(e) => {
              e.stopPropagation()
              handleCheckNow(row.original.id)
            }}
            disabled={runHealthCheck.isPending}
          >
            <Activity className="size-3.5" /> Check
          </Button>
        ),
        enableSorting: false,
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [healthByServer, runHealthCheck.isPending],
  )

  const data = servers ?? []

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader
        title="MCP Servers"
        description="Connect to Model Context Protocol servers to expose remote tools."
        action={
          <Button onClick={() => setWizardOpen(true)}>
            <Plus className="size-4" />
            New MCP server
          </Button>
        }
      />

      {!isLoading && data.length === 0 ? (
        <EmptyState
          icon={<Server className="size-6" />}
          title="No MCP servers yet"
          description="Add a server to import its tools and bind credentials."
          action={
            <Button onClick={() => setWizardOpen(true)}>
              <Plus className="size-4" />
              Add server
            </Button>
          }
        />
      ) : (
        <DataTable
          columns={columns}
          data={data}
          loading={isLoading}
          searchable
          searchPlaceholder="Search servers"
          onRowClick={(row) => setDetailId(row.id)}
          emptyTitle="No servers match your search"
        />
      )}

      <McpServerWizard open={wizardOpen} onOpenChange={setWizardOpen} />
      <McpServerDetailDialog
        serverId={detailId}
        open={!!detailId}
        onOpenChange={(open) => !open && setDetailId(null)}
      />
    </div>
  )
}

function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const deltaSec = Math.floor((Date.now() - then) / 1000)
  if (deltaSec < 60) return `${deltaSec}s ago`
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`
  return `${Math.floor(deltaSec / 86400)}d ago`
}
