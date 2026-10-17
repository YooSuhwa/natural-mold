'use client'

import type { ReactNode } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import type { useTranslations } from 'next-intl'

import { OriginBadge } from '@/components/marketplace/badges/origin-badge'
import { PublicationBadge } from '@/components/marketplace/badges/publication-badge'
import { Badge } from '@/components/ui/badge'
import { SkillEvaluationSummaryBadge } from '@/components/skill/skill-evaluation-summary-badge'
import { SkillHealthBadge } from '@/components/skill/skill-health-badge'
import { formatDisplayDate } from '@/lib/utils/display-format'
import type { Skill } from '@/lib/types/skill'

/** Columns of the skill list table; `actions` renders the row's action cell. */
export function buildSkillColumns({
  t,
  actions,
}: {
  t: ReturnType<typeof useTranslations<'skill'>>
  actions: (skill: Skill) => ReactNode
}): ColumnDef<Skill, unknown>[] {
  return [
    {
      accessorKey: 'name',
      header: t('columns.skill'),
      cell: ({ row }) => (
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{row.original.name}</p>
          <p className="moldy-ui-micro truncate font-mono text-muted-foreground">
            {row.original.slug}
            {row.original.version ? ` · v${row.original.version}` : ''}
          </p>
        </div>
      ),
    },
    {
      accessorKey: 'kind',
      header: t('columns.kind'),
      cell: ({ row }) => (
        <Badge variant="secondary" className="moldy-ui-micro">
          {t(`typeFilter.${row.original.kind}`)}
        </Badge>
      ),
    },
    {
      id: 'status',
      header: t('columns.status'),
      enableSorting: false,
      cell: ({ row }) => (
        // 게시/출처 배지는 구 카드에서 이관 — 목록에서 게시 상태를 잃지 않는다.
        <div className="flex flex-wrap items-center gap-1">
          <SkillHealthBadge health={row.original.health} />
          <OriginBadge summary={row.original.origin_summary} />
          <PublicationBadge summary={row.original.publication_summary} />
        </div>
      ),
    },
    {
      id: 'evaluation',
      header: t('columns.evaluation'),
      // 목업 계약(통과율 정렬) — 요약 pass_rate 기준, 미평가는 최하단.
      accessorFn: (skill) => skill.latest_evaluation_summary?.pass_rate ?? -1,
      cell: ({ row }) => (
        <SkillEvaluationSummaryBadge summary={row.original.latest_evaluation_summary} />
      ),
    },
    {
      accessorKey: 'used_by_count',
      header: t('columns.agents'),
      cell: ({ row }) => (
        <span className="text-sm tabular-nums">
          {t('agentsCount', { count: row.original.used_by_count })}
        </span>
      ),
    },
    {
      accessorKey: 'updated_at',
      header: t('columns.updatedAt'),
      cell: ({ row }) => (
        <span className="moldy-ui-micro text-muted-foreground">
          {formatDisplayDate(row.original.updated_at, { fallback: '' })}
        </span>
      ),
    },
    {
      id: 'actions',
      header: '',
      enableSorting: false,
      cell: ({ row }) => actions(row.original),
    },
  ]
}
