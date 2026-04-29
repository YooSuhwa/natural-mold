'use client'

import { useMemo, useState } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import { Plus, Server } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { PageHeader } from '@/components/shared/page-header'
import { DataTable } from '@/components/ui/data-table'
import { StatusChip } from '@/components/shared/status-chip'
import { EmptyState } from '@/components/shared/empty-state'
import { McpServerWizard } from '@/components/mcp/mcp-server-wizard'
import { McpServerDetailSheet } from '@/components/mcp/mcp-server-detail-sheet'
import { useMcpServers } from '@/lib/hooks/use-mcp-servers'
import type { McpServer } from '@/lib/types/mcp'

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

export default function McpServersPage() {
  const { data: servers, isLoading } = useMcpServers()
  const [wizardOpen, setWizardOpen] = useState(false)
  const [detailId, setDetailId] = useState<string | null>(null)

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
        accessorKey: 'status',
        header: 'Status',
        cell: ({ row }) => <StatusChip variant={row.original.status} />,
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
    ],
    [],
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
      <McpServerDetailSheet
        serverId={detailId}
        open={!!detailId}
        onOpenChange={(open) => !open && setDetailId(null)}
      />
    </div>
  )
}
