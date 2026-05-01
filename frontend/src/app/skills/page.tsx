'use client'

import { useMemo, useState } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import { Plus, BookOpen, LayoutGrid, Rows } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { PageHeader } from '@/components/shared/page-header'
import { DataTable, type FilterDef } from '@/components/ui/data-table'
import { EmptyState } from '@/components/shared/empty-state'
import { SkillCreateDialog } from '@/components/skill/skill-create-dialog'
import { SkillDetailDialog } from '@/components/skill/skill-detail-dialog'
import { useSkills } from '@/lib/hooks/use-skills'
import type { Skill } from '@/lib/types/skill'

type CreateTab = 'text' | 'package' | 'scratch'

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

export default function SkillsPage() {
  const { data: skills, isLoading } = useSkills()
  const [createOpen, setCreateOpen] = useState(false)
  const [createTab, setCreateTab] = useState<CreateTab>('text')
  const [detailId, setDetailId] = useState<string | null>(null)
  const [view, setView] = useState<'table' | 'grid'>('table')

  function openCreate(tab: CreateTab) {
    setCreateTab(tab)
    setCreateOpen(true)
  }

  const columns = useMemo<ColumnDef<Skill>[]>(
    () => [
      {
        accessorKey: 'name',
        header: 'Name',
        cell: ({ row }) => <span className="font-medium">{row.original.name}</span>,
      },
      {
        id: 'kind',
        accessorKey: 'kind',
        header: 'Kind',
        cell: ({ row }) => (
          <Badge variant="secondary" className="text-[10px]">
            {row.original.kind}
          </Badge>
        ),
        filterFn: 'equals',
      },
      {
        accessorKey: 'used_by_count',
        header: 'Agents',
        cell: ({ row }) => row.original.used_by_count,
      },
      {
        accessorKey: 'size_bytes',
        header: 'Size',
        cell: ({ row }) => `${row.original.size_bytes}b`,
      },
      {
        accessorKey: 'version',
        header: 'Version',
        cell: ({ row }) => row.original.version ?? '—',
      },
      {
        accessorKey: 'updated_at',
        header: 'Updated',
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground">
            {formatDate(row.original.updated_at)}
          </span>
        ),
      },
    ],
    [],
  )

  const filters = useMemo<FilterDef[]>(
    () => [
      {
        columnId: 'kind',
        label: 'Kind',
        options: [
          { value: 'text', label: 'Text' },
          { value: 'package', label: 'Package' },
        ],
      },
    ],
    [],
  )

  const data = skills ?? []

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader
        title="Skills"
        description="Markdown snippets and packages attached to agents."
        action={
          <Button onClick={() => openCreate('text')}>
            <Plus className="size-4" />
            New skill
          </Button>
        }
      />

      {!isLoading && data.length > 0 && (
        <div className="flex items-center justify-end gap-1 text-xs">
          <button
            type="button"
            onClick={() => setView('table')}
            className={`rounded p-1.5 ${view === 'table' ? 'bg-muted' : 'text-muted-foreground'}`}
            aria-label="Table view"
          >
            <Rows className="size-4" />
          </button>
          <button
            type="button"
            onClick={() => setView('grid')}
            className={`rounded p-1.5 ${view === 'grid' ? 'bg-muted' : 'text-muted-foreground'}`}
            aria-label="Grid view"
          >
            <LayoutGrid className="size-4" />
          </button>
        </div>
      )}

      {!isLoading && data.length === 0 ? (
        <EmptyState
          icon={<BookOpen className="size-6" />}
          title="No skills yet"
          description="Create a text snippet or upload a .skill package."
          action={
            <Button onClick={() => openCreate('text')}>
              <Plus className="size-4" />
              Create first skill
            </Button>
          }
        />
      ) : view === 'table' ? (
        <DataTable
          columns={columns}
          data={data}
          loading={isLoading}
          searchable
          searchPlaceholder="Search skills"
          filters={filters}
          onRowClick={(row) => setDetailId(row.id)}
          emptyTitle="No skills match your filters"
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {data.map((s) => (
            <Card
              key={s.id}
              role="button"
              tabIndex={0}
              onClick={() => setDetailId(s.id)}
              onKeyDown={(e) => (e.key === 'Enter' ? setDetailId(s.id) : null)}
              className="cursor-pointer transition-colors hover:border-primary/40"
            >
              <CardHeader>
                <div className="flex items-center gap-2">
                  <CardTitle className="text-sm">{s.name}</CardTitle>
                  <Badge variant="secondary" className="text-[10px]">
                    {s.kind}
                  </Badge>
                </div>
                <CardDescription className="line-clamp-2 text-xs">
                  {s.description ?? s.slug}
                </CardDescription>
              </CardHeader>
            </Card>
          ))}
        </div>
      )}

      <SkillCreateDialog
        key={`create-${createTab}`}
        open={createOpen}
        onOpenChange={setCreateOpen}
        initialTab={createTab}
        onCreated={(id) => setDetailId(id)}
      />
      <SkillDetailDialog
        skillId={detailId}
        open={!!detailId}
        onOpenChange={(open) => !open && setDetailId(null)}
      />
    </div>
  )
}
