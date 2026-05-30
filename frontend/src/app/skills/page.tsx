'use client'

import { useMemo, useState } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import { Plus, BookOpen, LayoutGrid, Rows } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { PageHeader } from '@/components/shared/page-header'
import { DataTable, type FilterDef } from '@/components/ui/data-table'
import { EmptyState } from '@/components/shared/empty-state'
import { SkillCreateDialog } from '@/components/skill/skill-create-dialog'
import { SkillDetailDialog } from '@/components/skill/skill-detail-dialog'
import { OriginBadge } from '@/components/marketplace/badges/origin-badge'
import { PublicationBadge } from '@/components/marketplace/badges/publication-badge'
import { PublishWizard } from '@/components/marketplace/publish-wizard'
import { useSkills } from '@/lib/hooks/use-skills'
import type { Skill } from '@/lib/types/skill'

type CreateTab = 'text' | 'package' | 'scratch'

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

function SkillViewToggle({
  view,
  onViewChange,
  label,
  tableLabel,
  gridLabel,
}: {
  view: 'table' | 'grid'
  onViewChange: (view: 'table' | 'grid') => void
  label: string
  tableLabel: string
  gridLabel: string
}) {
  return (
    <div
      role="tablist"
      aria-label={label}
      className="ml-auto inline-flex w-fit max-w-full gap-1 overflow-x-auto rounded-xl border border-border bg-muted/60 p-1"
    >
      <button
        type="button"
        role="tab"
        aria-selected={view === 'table'}
        aria-label={tableLabel}
        onClick={() => onViewChange('table')}
        className={`inline-flex h-8 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-3.5 text-sm transition-colors ${
          view === 'table'
            ? 'bg-background font-semibold text-foreground shadow-sm'
            : 'font-medium text-muted-foreground hover:text-foreground'
        }`}
      >
        <Rows className="size-4" />
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={view === 'grid'}
        aria-label={gridLabel}
        onClick={() => onViewChange('grid')}
        className={`inline-flex h-8 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-3.5 text-sm transition-colors ${
          view === 'grid'
            ? 'bg-background font-semibold text-foreground shadow-sm'
            : 'font-medium text-muted-foreground hover:text-foreground'
        }`}
      >
        <LayoutGrid className="size-4" />
      </button>
    </div>
  )
}

export default function SkillsPage() {
  const t = useTranslations('skill')
  const { data: skills, isLoading } = useSkills()
  const [createOpen, setCreateOpen] = useState(false)
  const [createTab, setCreateTab] = useState<CreateTab>('text')
  // Deep-link from /marketplace Open button: `/skills?detailId=...`.
  // useState lazy initializer runs once at mount (post-hydration on client,
  // safely returns null during SSR/prerender). Avoids effect+setState pattern
  // that the react-hooks/set-state-in-effect rule rejects.
  const [detailId, setDetailId] = useState<string | null>(() => {
    if (typeof window === 'undefined') return null
    return new URLSearchParams(window.location.search).get('detailId')
  })
  const [view, setView] = useState<'table' | 'grid'>('table')
  const [publishSkill, setPublishSkill] = useState<Skill | null>(null)

  function openCreate(tab: CreateTab) {
    setCreateTab(tab)
    setCreateOpen(true)
  }

  const columns = useMemo<ColumnDef<Skill>[]>(
    () => [
      {
        accessorKey: 'name',
        header: t('columns.skill'),
        cell: ({ row }) => {
          const skill = row.original
          return (
            <div className="min-w-[240px] space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-foreground">{skill.name}</span>
                {skill.version ? (
                  <span className="rounded-md bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                    v{skill.version}
                  </span>
                ) : null}
              </div>
              <p className="line-clamp-2 max-w-[360px] text-xs leading-5 text-muted-foreground">
                {skill.description ?? skill.slug}
              </p>
              <p className="font-mono text-[11px] text-muted-foreground/80">{skill.slug}</p>
            </div>
          )
        },
      },
      {
        id: 'kind',
        accessorKey: 'kind',
        header: t('columns.kind'),
        cell: ({ row }) => (
          <Badge variant="secondary" className="text-[10px]">
            {row.original.kind}
          </Badge>
        ),
        filterFn: 'equals',
      },
      {
        id: 'origin',
        header: t('columns.origin'),
        cell: ({ row }) => <OriginBadge summary={row.original.origin_summary} />,
      },
      {
        id: 'marketplace',
        header: t('columns.marketplace'),
        cell: ({ row }) => <PublicationBadge summary={row.original.publication_summary} />,
      },
      {
        accessorKey: 'used_by_count',
        header: t('columns.agents'),
        cell: ({ row }) => row.original.used_by_count,
      },
      {
        accessorKey: 'updated_at',
        header: t('columns.updatedAt'),
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground">
            {formatDate(row.original.updated_at)}
          </span>
        ),
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => {
          const state = row.original.publication_summary?.state
          const canPublish = !state || state === 'not_published'
          if (!canPublish) return null
          return (
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation()
                setPublishSkill(row.original)
              }}
            >
              {t('actions.publish')}
            </Button>
          )
        },
      },
    ],
    [t],
  )

  const filters = useMemo<FilterDef[]>(
    () => [
      {
        columnId: 'kind',
        label: t('filters.kind'),
        options: [
          { value: 'text', label: t('typeFilter.text') },
          { value: 'package', label: t('typeFilter.package') },
        ],
      },
    ],
    [t],
  )

  const data = skills ?? []
  const showViewToggle = !isLoading && data.length > 0

  return (
    <div className="flex flex-1 flex-col overflow-auto bg-gradient-to-b from-emerald-50/40 via-background to-background dark:from-emerald-950/15 dark:via-background dark:to-background">
      <div className="mx-auto flex w-full max-w-[1180px] flex-1 flex-col gap-6 px-6 py-7 pb-20 md:px-8">
        <PageHeader
          title={t('title')}
          description={t('description')}
          action={
            <div className="flex max-w-full flex-wrap items-center justify-end gap-2">
              {showViewToggle ? (
                <SkillViewToggle
                  view={view}
                  onViewChange={setView}
                  label={t('viewMode.label')}
                  tableLabel={t('viewMode.table')}
                  gridLabel={t('viewMode.grid')}
                />
              ) : null}
              <Button onClick={() => openCreate('text')}>
                <Plus className="size-4" />
                {t('new')}
              </Button>
            </div>
          }
        />

        {!isLoading && data.length === 0 ? (
          <EmptyState
            icon={<BookOpen className="size-6" />}
            title={t('empty.title')}
            description={t('empty.description')}
            action={
              <Button onClick={() => openCreate('text')}>
                <Plus className="size-4" />
                {t('firstSkill')}
              </Button>
            }
          />
        ) : view === 'table' ? (
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
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {data.map((s) => (
              <Card
                key={s.id}
                role="button"
                tabIndex={0}
                onClick={() => setDetailId(s.id)}
                onKeyDown={(e) => (e.key === 'Enter' ? setDetailId(s.id) : null)}
                className="cursor-pointer border border-border bg-card transition-all duration-150 hover:-translate-y-px hover:border-emerald-200 hover:shadow-md dark:hover:border-emerald-500/30"
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
          onOpenChange={(open) => {
            if (open) return
            setDetailId(null)
            // /marketplace에서 ``?detailId=...`` deep-link로 진입한 경우, dialog
            // 닫을 때 URL의 query string도 함께 정리한다. history.replaceState로
            // route 자체는 다시 그리지 않아 list scroll/state 보존.
            if (typeof window !== 'undefined' && window.location.search) {
              window.history.replaceState(null, '', '/skills')
            }
          }}
        />

        <PublishWizard
          skill={publishSkill}
          open={!!publishSkill}
          onOpenChange={(open) => !open && setPublishSkill(null)}
        />
      </div>
    </div>
  )
}
