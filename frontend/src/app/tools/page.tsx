'use client'

import { useMemo, useState } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import { Plus, Wrench } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { PageHeader } from '@/components/shared/page-header'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { DataTable } from '@/components/ui/data-table'
import { DomainIcon } from '@/components/shared/icon'
import { StatusChip } from '@/components/shared/status-chip'
import { EmptyState } from '@/components/shared/empty-state'
import { ToolCatalog } from '@/components/tool/tool-catalog'
import { ToolCreateDialog } from '@/components/tool/tool-create-dialog'
import { ToolDetailSheet } from '@/components/tool/tool-detail-sheet'
import { useTools, useToolTypes } from '@/lib/hooks/use-tools'
import { useCredentials } from '@/lib/hooks/use-credentials'
import type { ToolDefinition, ToolInstance } from '@/lib/types/tool'

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

export default function ToolsPage() {
  const { data: tools, isLoading } = useTools()
  const { data: definitions } = useToolTypes()
  const { data: credentials } = useCredentials()
  const [tab, setTab] = useState<'catalog' | 'manage'>('catalog')
  const [pickedDefinition, setPickedDefinition] = useState<ToolDefinition | null>(null)
  const [detailId, setDetailId] = useState<string | null>(null)

  const definitionLabels = useMemo(() => {
    const m = new Map<string, ToolDefinition>()
    definitions?.forEach((d) => m.set(d.key, d))
    return m
  }, [definitions])

  const credentialMap = useMemo(() => {
    const m = new Map<string, string>()
    credentials?.forEach((c) => m.set(c.id, c.status))
    return m
  }, [credentials])

  const columns = useMemo<ColumnDef<ToolInstance>[]>(
    () => [
      {
        id: 'name',
        accessorKey: 'name',
        header: 'Name',
        cell: ({ row }) => <span className="font-medium">{row.original.name}</span>,
      },
      {
        id: 'definition_key',
        accessorKey: 'definition_key',
        header: 'Type',
        cell: ({ row }) => {
          const def = definitionLabels.get(row.original.definition_key)
          return (
            <span className="inline-flex items-center gap-2">
              <DomainIcon
                iconId={def?.icon_id ?? row.original.definition_key}
                className="size-4"
              />
              <span>{def?.display_name ?? row.original.definition_key}</span>
            </span>
          )
        },
      },
      {
        id: 'credential',
        header: 'Credential',
        cell: ({ row }) => {
          const id = row.original.credential_id
          if (!id) return <span className="text-xs text-muted-foreground">—</span>
          const status = credentialMap.get(id) ?? 'unknown'
          return <StatusChip variant={status} />
        },
      },
      {
        id: 'enabled',
        accessorKey: 'enabled',
        header: 'State',
        cell: ({ row }) => (
          <StatusChip variant={row.original.enabled ? 'active' : 'disabled'} />
        ),
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
    ],
    [definitionLabels, credentialMap],
  )

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader
        title="Tools"
        description="Pick a tool from the catalog or manage configured instances."
      />

      <Tabs value={tab} onValueChange={(v) => setTab(v as 'catalog' | 'manage')}>
        <TabsList>
          <TabsTrigger value="catalog">Catalog</TabsTrigger>
          <TabsTrigger value="manage">
            Manage ({tools?.length ?? 0})
          </TabsTrigger>
        </TabsList>

        <TabsContent value="catalog" className="mt-4">
          <ToolCatalog onPick={setPickedDefinition} />
        </TabsContent>

        <TabsContent value="manage" className="mt-4">
          {!isLoading && (tools ?? []).length === 0 ? (
            <EmptyState
              icon={<Wrench className="size-6" />}
              title="No tools yet"
              description="Pick one from the catalog to get started."
              action={
                <Button onClick={() => setTab('catalog')}>
                  <Plus className="size-4" />
                  Browse catalog
                </Button>
              }
            />
          ) : (
            <DataTable
              columns={columns}
              data={tools ?? []}
              loading={isLoading}
              searchable
              searchPlaceholder="Search tools"
              onRowClick={(row) => setDetailId(row.id)}
              emptyTitle="No tools match your search"
            />
          )}
        </TabsContent>
      </Tabs>

      <ToolCreateDialog
        definition={pickedDefinition}
        open={!!pickedDefinition}
        onOpenChange={(open) => !open && setPickedDefinition(null)}
        onCreated={() => setTab('manage')}
      />
      <ToolDetailSheet
        toolId={detailId}
        open={!!detailId}
        onOpenChange={(open) => !open && setDetailId(null)}
      />
    </div>
  )
}
