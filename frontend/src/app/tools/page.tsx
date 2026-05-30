'use client'

import { useMemo, useState } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import { Plus, Wrench } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { PageHeader } from '@/components/shared/page-header'
import { DataTable } from '@/components/ui/data-table'
import { DomainIcon } from '@/components/shared/icon'
import { StatusChip } from '@/components/shared/status-chip'
import { EmptyState } from '@/components/shared/empty-state'
import { ToolCatalog } from '@/components/tool/tool-catalog'
import { ToolCreateDialog } from '@/components/tool/tool-create-dialog'
import { ToolDetailDialog } from '@/components/tool/tool-detail-dialog'
import { cn } from '@/lib/utils'
import { useTools, useToolTypes } from '@/lib/hooks/use-tools'
import { useCredentials } from '@/lib/hooks/use-credentials'
import type { ToolDefinition, ToolInstance } from '@/lib/types/tool'

type Tab = 'catalog' | 'manage'

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

export default function ToolsPage() {
  const t = useTranslations('tool.page')
  const { data: tools, isLoading } = useTools()
  const { data: definitions } = useToolTypes()
  const { data: credentials } = useCredentials()
  const [tab, setTab] = useState<Tab>('catalog')
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
        header: t('columns.name'),
        cell: ({ row }) => <span className="font-medium">{row.original.name}</span>,
      },
      {
        id: 'definition_key',
        accessorKey: 'definition_key',
        header: t('columns.type'),
        cell: ({ row }) => {
          const def = definitionLabels.get(row.original.definition_key)
          return (
            <span className="inline-flex items-center gap-2">
              <DomainIcon iconId={def?.icon_id ?? row.original.definition_key} className="size-4" />
              <span>{def?.display_name ?? row.original.definition_key}</span>
            </span>
          )
        },
      },
      {
        id: 'credential',
        header: t('columns.credential'),
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
        header: t('columns.status'),
        cell: ({ row }) => <StatusChip variant={row.original.enabled ? 'active' : 'disabled'} />,
      },
      {
        id: 'last_used_at',
        accessorKey: 'last_used_at',
        header: t('columns.lastUsed'),
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground">
            {formatDate(row.original.last_used_at)}
          </span>
        ),
      },
    ],
    [definitionLabels, credentialMap, t],
  )

  const tabs: { value: Tab; label: string }[] = [
    { value: 'catalog', label: t('tabs.catalog') },
    { value: 'manage', label: t('tabs.manage', { count: tools?.length ?? 0 }) },
  ]

  return (
    <div className="flex flex-1 flex-col overflow-auto bg-gradient-to-b from-emerald-50/40 via-background to-background dark:from-emerald-950/15 dark:via-background dark:to-background">
      <div className="mx-auto flex w-full max-w-[1180px] flex-1 flex-col gap-6 px-6 py-7 pb-20 md:px-8">
        <PageHeader
          title={t('title')}
          description={t('description')}
        />

        <div
          role="tablist"
          aria-label={t('viewMode')}
          className="inline-flex w-fit max-w-full gap-1 overflow-x-auto rounded-xl border border-border bg-muted/60 p-1"
        >
          {tabs.map((t) => {
            const isActive = tab === t.value
            return (
              <button
                key={t.value}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => setTab(t.value)}
                className={cn(
                  'inline-flex h-8 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-3.5 text-sm transition-colors',
                  isActive
                    ? 'bg-background font-semibold text-foreground shadow-sm'
                    : 'font-medium text-muted-foreground hover:text-foreground',
                )}
              >
                {t.label}
              </button>
            )
          })}
        </div>

        {tab === 'catalog' ? (
          <ToolCatalog onPick={setPickedDefinition} />
        ) : !isLoading && (tools ?? []).length === 0 ? (
          <EmptyState
            icon={<Wrench className="size-6" />}
            title={t('empty.title')}
            description={t('empty.description')}
            action={
              <Button onClick={() => setTab('catalog')}>
                <Plus className="size-4" />
                {t('empty.action')}
              </Button>
            }
          />
        ) : (
          <DataTable
            columns={columns}
            data={tools ?? []}
            loading={isLoading}
            searchable
            searchPlaceholder={t('searchPlaceholder')}
            onRowClick={(row) => setDetailId(row.id)}
            emptyTitle={t('empty.filtered')}
          />
        )}

        <ToolCreateDialog
          definition={pickedDefinition}
          open={!!pickedDefinition}
          onOpenChange={(open) => !open && setPickedDefinition(null)}
          onCreated={() => setTab('manage')}
        />
        <ToolDetailDialog
          toolId={detailId}
          open={!!detailId}
          onOpenChange={(open) => !open && setDetailId(null)}
        />
      </div>
    </div>
  )
}
