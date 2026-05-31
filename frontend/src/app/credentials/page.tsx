'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Plus } from 'lucide-react'
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
  const t = useTranslations('credentials.page')
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
        header: t('columns.name'),
        cell: ({ row }) => <span className="font-medium">{row.original.name}</span>,
      },
      {
        id: 'definition_key',
        accessorKey: 'definition_key',
        header: t('columns.type'),
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
        header: t('columns.status'),
        cell: ({ row }) => <StatusChip variant={row.original.status} />,
        filterFn: 'equals',
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
      {
        id: 'last_tested_at',
        accessorKey: 'last_tested_at',
        header: t('columns.lastTested'),
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground">
            {formatDate(row.original.last_tested_at)}
          </span>
        ),
      },
    ],
    [definitionLabels, t],
  )

  const filters = useMemo<FilterDef[]>(
    () => [
      {
        columnId: 'definition_key',
        label: t('filters.type'),
        options: (definitions ?? []).map((d) => ({
          value: d.key,
          label: d.display_name,
        })),
      },
      {
        columnId: 'status',
        label: t('filters.status'),
        options: [
          { value: 'active', label: t('statuses.active') },
          { value: 'auth_needed', label: t('statuses.authNeeded') },
          { value: 'expired', label: t('statuses.expired') },
          { value: 'disabled', label: t('statuses.disabled') },
        ],
      },
    ],
    [definitions, t],
  )

  const data = credentials ?? []

  return (
    <div className="flex flex-1 flex-col overflow-auto bg-gradient-to-b from-emerald-50/40 via-background to-background dark:from-emerald-950/15 dark:via-background dark:to-background">
      <div className="mx-auto flex w-full max-w-[1180px] flex-1 flex-col gap-6 px-6 py-7 pb-20 md:px-8">
        <PageHeader
          title={t('title')}
          description={t('description')}
          action={
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="size-4" />
              {t('new')}
            </Button>
          }
        />

        {!isLoading && data.length === 0 ? (
          <EmptyState
            iconId="credential"
            title={t('empty.title')}
            description={t('empty.description')}
            action={
              <Button onClick={() => setCreateOpen(true)}>
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
            filters={filters}
            onRowClick={(row) => setDetailId(row.id)}
            emptyTitle={t('empty.filtered')}
          />
        )}

        <CredentialCreateModal open={createOpen} onOpenChange={setCreateOpen} />
        <CredentialDetailDialog
          credentialId={detailId}
          open={!!detailId}
          onOpenChange={(open) => !open && setDetailId(null)}
        />
      </div>
    </div>
  )
}
