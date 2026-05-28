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
        header: '이름',
        cell: ({ row }) => <span className="font-medium">{row.original.name}</span>,
      },
      {
        id: 'definition_key',
        accessorKey: 'definition_key',
        header: '종류',
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
        header: '상태',
        cell: ({ row }) => <StatusChip variant={row.original.status} />,
        filterFn: 'equals',
      },
      {
        id: 'last_used_at',
        accessorKey: 'last_used_at',
        header: '최근 사용',
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground">
            {formatDate(row.original.last_used_at)}
          </span>
        ),
      },
      {
        id: 'last_tested_at',
        accessorKey: 'last_tested_at',
        header: '최근 테스트',
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
        label: '종류',
        options: (definitions ?? []).map((d) => ({
          value: d.key,
          label: d.display_name,
        })),
      },
      {
        columnId: 'status',
        label: '상태',
        options: [
          { value: 'active', label: '활성' },
          { value: 'auth_needed', label: '인증 필요' },
          { value: 'expired', label: '만료' },
          { value: 'disabled', label: '비활성' },
        ],
      },
    ],
    [definitions],
  )

  const data = credentials ?? []

  return (
    <div className="flex flex-1 flex-col overflow-auto bg-gradient-to-b from-emerald-50/40 via-background to-background dark:from-emerald-950/15 dark:via-background dark:to-background">
      <div className="mx-auto flex w-full max-w-[1180px] flex-1 flex-col gap-6 px-6 py-7 pb-20 md:px-8">
        <PageHeader
          title="자격증명"
          description="도구와 MCP 서버에서 사용하는 API 키와 OAuth 권한을 관리하세요."
          action={
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="size-4" />새 자격증명
            </Button>
          }
        />

        {!isLoading && data.length === 0 ? (
          <EmptyState
            icon={<KeyRound className="size-6" />}
            title="아직 자격증명이 없어요"
            description="API 키나 OAuth 권한을 추가하면 도구가 인증할 수 있어요."
            action={
              <Button onClick={() => setCreateOpen(true)}>
                <Plus className="size-4" />
                자격증명 추가
              </Button>
            }
          />
        ) : (
          <DataTable
            columns={columns}
            data={data}
            loading={isLoading}
            searchable
            searchPlaceholder="자격증명 검색"
            filters={filters}
            onRowClick={(row) => setDetailId(row.id)}
            emptyTitle="조건에 맞는 자격증명이 없어요"
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
