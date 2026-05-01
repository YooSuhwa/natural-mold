'use client'

import { useMemo, useState } from 'react'
import { Plus, KeyRound } from 'lucide-react'
import type { ColumnDef } from '@tanstack/react-table'

import { Button } from '@/components/ui/button'
import { PageHeader } from '@/components/shared/page-header'
import { DataTable, type FilterDef } from '@/components/ui/data-table'
import { StatusChip } from '@/components/shared/status-chip'
import { DomainIcon } from '@/components/shared/icon'
import { EmptyState } from '@/components/shared/empty-state'
import { CredentialCreateModal } from '@/components/credential/credential-create-modal'
import { CredentialDetailDialog } from '@/components/credential/credential-detail-dialog'
import { useCredentials, useCredentialTypes } from '@/lib/hooks/use-credentials'
import type { Credential } from '@/lib/types/credential'

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

export default function CredentialsPage() {
  const { data: credentials, isLoading } = useCredentials()
  const { data: definitions } = useCredentialTypes()
  const [createOpen, setCreateOpen] = useState(false)
  const [detailId, setDetailId] = useState<string | null>(null)

  const definitionLabels = useMemo(() => {
    const map = new Map<string, string>()
    definitions?.forEach((d) => map.set(d.key, d.display_name))
    return map
  }, [definitions])

  const columns = useMemo<ColumnDef<Credential>[]>(
    () => [
      {
        id: 'name',
        accessorKey: 'name',
        header: 'Name',
        cell: ({ row }) => (
          <span className="font-medium">{row.original.name}</span>
        ),
      },
      {
        id: 'definition_key',
        accessorKey: 'definition_key',
        header: 'Type',
        cell: ({ row }) => {
          const key = row.original.definition_key
          return (
            <span className="inline-flex items-center gap-2">
              <DomainIcon iconId={key} className="size-4" />
              <span className="text-sm">{definitionLabels.get(key) ?? key}</span>
            </span>
          )
        },
        filterFn: 'equals',
      },
      {
        id: 'status',
        accessorKey: 'status',
        header: 'Status',
        cell: ({ row }) => <StatusChip variant={row.original.status} />,
        filterFn: 'equals',
      },
      {
        id: 'last_used_at',
        accessorKey: 'last_used_at',
        header: 'Last used',
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground">
            {formatDate(row.original.last_used_at)}
          </span>
        ),
      },
      {
        id: 'last_tested_at',
        accessorKey: 'last_tested_at',
        header: 'Last tested',
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground">
            {formatDate(row.original.last_tested_at)}
          </span>
        ),
      },
    ],
    [definitionLabels],
  )

  const filters = useMemo<FilterDef[]>(
    () => [
      {
        columnId: 'definition_key',
        label: 'Type',
        options: (definitions ?? []).map((d) => ({
          value: d.key,
          label: d.display_name,
        })),
      },
      {
        columnId: 'status',
        label: 'Status',
        options: [
          { value: 'active', label: 'Active' },
          { value: 'auth_needed', label: 'Auth needed' },
          { value: 'expired', label: 'Expired' },
          { value: 'disabled', label: 'Disabled' },
        ],
      },
    ],
    [definitions],
  )

  const data = credentials ?? []

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader
        title="Credentials"
        description="API keys and OAuth grants used by tools and MCP servers."
        action={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="size-4" />
            New credential
          </Button>
        }
      />

      {!isLoading && data.length === 0 ? (
        <EmptyState
          icon={<KeyRound className="size-6" />}
          title="No credentials yet"
          description="Add an API key or OAuth grant so your tools can authenticate."
          action={
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="size-4" />
              Add credential
            </Button>
          }
        />
      ) : (
        <DataTable
          columns={columns}
          data={data}
          loading={isLoading}
          searchable
          searchPlaceholder="Search credentials"
          filters={filters}
          onRowClick={(row) => setDetailId(row.id)}
          emptyTitle="No credentials match your filters"
        />
      )}

      <CredentialCreateModal open={createOpen} onOpenChange={setCreateOpen} />
      <CredentialDetailDialog
        credentialId={detailId}
        open={!!detailId}
        onOpenChange={(open) => !open && setDetailId(null)}
      />
    </div>
  )
}
